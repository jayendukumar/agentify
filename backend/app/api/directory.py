"""Epic 37, US37.10-37.11: what an admin manages about the IdP connection
-- the SCIM tokens the IdP provisions with, and which groups grant which
roles (previewed before they're saved)."""

import hashlib
import secrets

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.auth import audit, directory
from app.config import get_settings
from app.db.models import ContextEntityModel, ScimTokenModel
from app.ids import new_id, utcnow
from app.schemas.directory import (
    GrantOut,
    GroupMapping,
    GroupMappings,
    KnownGroup,
    MappingsSaved,
    RoleChangeOut,
    ScimSetup,
    ScimTokenCreate,
    ScimTokenIssued,
    ScimTokenOut,
)

from .deps import DbDep, UsersManageDep

router = APIRouter(prefix="/api/directory", tags=["directory"])


def _token_out(row: ScimTokenModel) -> ScimTokenOut:
    return ScimTokenOut(id=row.id, label=row.label, created_at=row.created_at, created_by=row.created_by,
                        last_used_at=row.last_used_at, revoked_at=row.revoked_at)


@router.get("/scim", response_model=ScimSetup)
def scim_setup(db: DbDep, user: UsersManageDep) -> ScimSetup:
    del user
    settings = get_settings()
    rows = db.scalars(select(ScimTokenModel).order_by(ScimTokenModel.created_at.desc()))
    return ScimSetup(base_url=settings.api_public_url.rstrip("/") + "/scim/v2", link_claim=settings.scim_link_claim,
                     tokens=[_token_out(r) for r in rows])


@router.post("/scim/tokens", response_model=ScimTokenIssued, status_code=201)
def create_scim_token(body: ScimTokenCreate, db: DbDep, request: Request, user: UsersManageDep) -> ScimTokenIssued:
    """The token is in this response only; afterwards just its hash exists."""
    token = "scim_" + secrets.token_urlsafe(32)
    row = ScimTokenModel(id=new_id("scimtok"), token_hash=hashlib.sha256(token.encode()).hexdigest(),
                         label=body.label.strip(), created_at=utcnow().replace(tzinfo=None), created_by=user.id)
    db.add(row)
    db.flush()
    audit.record(db, actor=user, action="scim_token_created", object_type="scim_token", object_id=row.id,
                 request=request, detail={"label": row.label})
    return ScimTokenIssued(**_token_out(row).model_dump(), token=token)


@router.delete("/scim/tokens/{token_id}", status_code=204)
def revoke_scim_token(token_id: str, db: DbDep, request: Request, user: UsersManageDep) -> None:
    row = db.get(ScimTokenModel, token_id)
    if row is None or row.revoked_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such active token")
    row.revoked_at = utcnow().replace(tzinfo=None)
    audit.record(db, actor=user, action="scim_token_revoked", object_type="scim_token", object_id=row.id,
                 request=request, detail={"label": row.label})


@router.get("/groups", response_model=list[KnownGroup])
def known_groups(db: DbDep, user: UsersManageDep) -> list[KnownGroup]:
    del user
    return [KnownGroup(**g) for g in directory.known_groups(db)]


def _scope_name(db, scope_type: str, scope_id: str) -> str | None:
    if scope_type != "business_unit":
        return None
    unit = db.get(ContextEntityModel, scope_id)
    return unit.name if unit is not None else scope_id


def _mapping_out(db, m: directory.Mapping) -> GroupMapping:
    return GroupMapping(group=m.group, role=m.role, scope_type=m.scope_type, scope_id=m.scope_id,
                        scope_name=_scope_name(db, m.scope_type, m.scope_id))


def _changes_out(db, changes: list[directory.RoleChange]) -> list[RoleChangeOut]:
    def grants(items):
        return [GrantOut(role=r, scope_type=t, scope_id=i, scope_name=_scope_name(db, t, i)) for r, t, i in items]

    return [RoleChangeOut(user_id=c.user_id, user_name=c.user_name, gained=grants(c.gained), lost=grants(c.lost))
            for c in sorted(changes, key=lambda c: c.user_name.casefold())]


def _proposed(db, body: GroupMappings) -> list[directory.Mapping]:
    try:
        return directory.validate(db, [directory.Mapping(m.group, m.role, m.scope_type, m.scope_id) for m in body.mappings])
    except directory.MappingError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/group-mappings", response_model=list[GroupMapping])
def list_group_mappings(db: DbDep, user: UsersManageDep) -> list[GroupMapping]:
    del user
    return [_mapping_out(db, m) for m in directory.load(db, get_settings())]


@router.post("/group-mappings/preview", response_model=list[RoleChangeOut])
def preview_group_mappings(body: GroupMappings, db: DbDep, user: UsersManageDep) -> list[RoleChangeOut]:
    """US37.11: who would gain or lose which role. Changes nothing."""
    del user
    return _changes_out(db, directory.preview(db, _proposed(db, body)))


@router.put("/group-mappings", response_model=MappingsSaved)
def save_group_mappings(body: GroupMappings, db: DbDep, request: Request, user: UsersManageDep) -> MappingsSaved:
    """Replaces every mapping and applies them to everyone now."""
    proposed = _proposed(db, body)
    directory.load(db, get_settings())  # seed first, so saving counts as the deliberate change it is
    changes = directory.replace(db, proposed, actor=user, request=request)
    return MappingsSaved(mappings=[_mapping_out(db, m) for m in proposed], changes=_changes_out(db, changes))
