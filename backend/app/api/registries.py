from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import secret_store
from app.auth import audit
from app.db.models import RegistryModel, UserModel
from app.ids import new_id
from app.registry import manager
from app.registry.base import RegistryAuthError, RegistryConnectorError, RegistryEntry, RegistryUnreachableError
from app.schemas.registry import (
    RegistryEntryOut,
    RegistryPushRequest,
    RegistrySearchResult,
    RegistrySetting,
    RegistrySettings,
    RegistryStatus,
)
from app.tenancy.identity import current_slug

from .deps import AgentPublishDep, CurrentUserDep, DbDep, OrgAdminDep, RegistriesDep

# Top-level, not nested under /api/processes/{process_id} -- a registry is
# explicitly cross-process (US13.5: search across everything registered,
# not one process's agents), unlike blueprint/gap-findings/versions.
router = APIRouter(prefix="/api/registries", tags=["registries"])


def _resolve_pushed_by_name(db: Session, user_id: str | None) -> str | None:
    if user_id is None:
        return None
    user = db.get(UserModel, user_id)
    return user.name if user else None


def _entry_out(entry: RegistryEntry, db: Session) -> RegistryEntryOut:
    return RegistryEntryOut(
        id=entry.id,
        registry_name=entry.registry_name,
        agent_name=entry.agent_name,
        tags=entry.tags,
        definition=entry.definition,
        pushed_at=entry.pushed_at,
        source_process_id=entry.source_process_id,
        source_node_ids=entry.source_node_ids,
        pushed_by=entry.pushed_by,
        pushed_by_name=_resolve_pushed_by_name(db, entry.pushed_by),
    )


@router.get("", response_model=list[RegistryStatus])
def list_registries(registries: RegistriesDep, user: CurrentUserDep) -> list[RegistryStatus]:
    del user
    statuses = []
    for connector in registries.values():
        health = connector.check_health()
        statuses.append(
            RegistryStatus(
                name=connector.name,
                type=connector.type,
                reachable=health.reachable,
                authenticated=health.authenticated,
                message=health.message,
            )
        )
    return statuses


@router.get("/search", response_model=RegistrySearchResult)
def search_registries(
    registries: RegistriesDep, db: DbDep, user: CurrentUserDep, q: str = Query("", alias="q")
) -> RegistrySearchResult:
    del user
    entries: list[RegistryEntryOut] = []
    registry_errors: dict[str, str] = {}
    for connector in registries.values():
        try:
            found = connector.search(q)
        except RegistryConnectorError as exc:
            registry_errors[connector.name] = str(exc)
            continue
        entries.extend(_entry_out(e, db) for e in found)
    return RegistrySearchResult(entries=entries, registry_errors=registry_errors)


@router.post("/{registry_name}/push", response_model=RegistryEntryOut)
def push_to_registry(
    registry_name: str, body: RegistryPushRequest, registries: RegistriesDep, db: DbDep, user: AgentPublishDep
) -> RegistryEntryOut:
    connector = registries.get(registry_name)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown registry '{registry_name}'")

    try:
        entry = connector.push(
            agent_name=body.agent_name,
            definition=body.definition,
            tags=body.tags,
            source_process_id=body.source_process_id,
            source_node_ids=body.source_node_ids,
            pushed_by=user.id,
        )
    except RegistryAuthError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Registry '{registry_name}' rejected credentials: {exc}"
        ) from exc
    except RegistryUnreachableError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Registry '{registry_name}' is unreachable: {exc}"
        ) from exc

    return _entry_out(entry, db)


# ---- Epic 38, US38.11: which registries this organization uses ------------------------

admin_router = APIRouter(prefix="/api/organization/registries", tags=["registries"])

_SECRET_WORDS = ("token", "password", "secret", "api_key", "apikey", "credential")


def _settings_out(db: Session) -> RegistrySettings:
    configs, source = manager.configured(db)
    rows = {r.name: r for r in db.scalars(select(RegistryModel))}
    return RegistrySettings(
        registries=[RegistrySetting(name=c.name, type=c.type, config=rows[c.name].config if c.name in rows else {})
                    for c in configs],
        source=source, types=sorted(manager.CONNECTOR_TYPES),
    )


@admin_router.get("", response_model=RegistrySettings)
def get_registry_settings(db: DbDep, user: OrgAdminDep) -> RegistrySettings:
    del user
    return _settings_out(db)


@admin_router.put("", response_model=RegistrySettings)
def set_registry_settings(body: RegistrySettings, db: DbDep, user: OrgAdminDep, request: Request) -> RegistrySettings:
    """Replaces this organization's registries. An empty list goes back to
    the default. Entries already pushed to a removed registry stay, but
    aren't searched."""
    names = [r.name for r in body.registries]
    if len(set(names)) != len(names):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Each registry needs its own name")
    slug = current_slug() or ""
    for registry in body.registries:
        if registry.type not in manager.CONNECTOR_TYPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail=f"Unknown registry type '{registry.type}' (known: {', '.join(sorted(manager.CONNECTOR_TYPES))})")
        for key, value in registry.config.items():
            if key.endswith("_ref"):
                try:
                    secret_store.check_ref(str(value), slug, example="secret://tenant/registry_token")
                except secret_store.SecretRefError as exc:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{registry.name}: {key}: {exc}") from exc
            elif any(word in key.lower() for word in _SECRET_WORDS):
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail=f"{registry.name}: don't store '{key}' -- reference the secret as "
                                           f"{key}_ref, like secret://tenant/registry_token")
    before = [r.name for r in db.scalars(select(RegistryModel))]
    db.execute(delete(RegistryModel))
    for registry in body.registries:
        db.add(RegistryModel(id=new_id("reg"), name=registry.name, type=registry.type, config=registry.config,
                             created_by=user.id))
    db.flush()
    audit.record(db, actor=user, action="registries_changed", request=request,
                 detail={"from": before, "to": names})
    return _settings_out(db)
