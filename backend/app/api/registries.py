from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import UserModel
from app.registry.base import RegistryAuthError, RegistryConnectorError, RegistryEntry, RegistryUnreachableError
from app.schemas.registry import RegistryEntryOut, RegistryPushRequest, RegistrySearchResult, RegistryStatus

from .deps import CurrentUserDep, DbDep, EditorDep, RegistriesDep

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
    registry_name: str, body: RegistryPushRequest, registries: RegistriesDep, db: DbDep, user: EditorDep
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
