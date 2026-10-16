"""Epic 37, US37.10-37.11: what an admin manages about the IdP connection
-- the SCIM tokens the IdP provisions with, and which groups grant which
roles (previewed before they're saved). Epic 38, US38.6: and which IdP
this organization signs in with."""

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app import secret_store
from app.auth import audit, directory, oidc, sessions, support
from app.config import get_settings
from app.db.models import ContextEntityModel, ScimTokenModel, SessionModel, SupportGrantModel, TenantIdentityModel
from app.ids import new_id, utcnow
from app.tenancy import identity
from app.tenancy.context import current_tenant_id
from app.schemas.directory import (
    ConnectionTestOut,
    IdentitySettingsIn,
    IdentitySettingsOut,
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
    SupportGrantIn,
    SupportGrantOut,
)

from .deps import DbDep, UsersManageDep

router = APIRouter(prefix="/api/directory", tags=["directory"])
IdpHttpDep = Annotated[httpx.Client | None, Depends(oidc.get_idp_http)]


# ---- Epic 38, US38.6: the organization's identity provider ---------------------


def _identity_out(db, config: identity.IdentityConfig) -> IdentitySettingsOut:
    row = db.scalar(select(TenantIdentityModel))
    slug = identity.tenant_slug(db, config.tenant_id) if config.tenant_id else ""
    return IdentitySettingsOut(
        auth_mode=config.auth_mode, source=config.source, issuer=config.issuer, discovery_url=config.discovery_url,
        client_id=config.client_id, client_secret_ref=config.client_secret_ref, display_name=config.display_name,
        scopes=config.scopes, acr_values=config.acr_values, groups_claim=config.groups_claim,
        jit_provisioning=config.jit_provisioning, require_mfa=config.require_mfa,
        mfa_amr_values=list(config.mfa_amr_values), mfa_acr_values=list(config.mfa_acr_values),
        scim_link_claim=config.scim_link_claim, secret_configured=bool(config.client_secret),
        secret_location=secret_store.where_to_put(config.client_secret_ref or identity.DEFAULT_SECRET_REF, slug or ""),
        redirect_uri=config.redirect_uri,
        post_logout_redirect_uri=config.frontend_url.rstrip("/") + "/",
        backchannel_logout_url=config.backchannel_logout_url, scim_base_url=config.scim_base_url,
        updated_at=row.updated_at if row else None, updated_by=row.updated_by if row else None,
    )


def _candidate(db, body: IdentitySettingsIn) -> identity.IdentityConfig:
    """The configuration the body would give, refused if it can't be saved."""
    current = identity.resolve(db)
    values = body.model_dump()
    try:
        identity.validate(values, identity.tenant_slug(db, current.tenant_id) or "", get_settings())
    except identity.IdentityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    host = identity.current_host() or "localhost"
    return identity.candidate(values, current.tenant_id, get_settings(), host)


def _test_out(check: oidc.ConnectionCheck, config: identity.IdentityConfig) -> ConnectionTestOut:
    doc = check.discovery
    return ConnectionTestOut(
        ok=check.ok, issuer=doc.get("issuer"), authorization_endpoint=doc.get("authorization_endpoint"),
        token_endpoint=doc.get("token_endpoint"), end_session_endpoint=doc.get("end_session_endpoint"),
        signing_keys=check.signing_keys, secret_configured=bool(config.client_secret), problems=check.problems,
    )


@router.get("/identity", response_model=IdentitySettingsOut)
def get_identity_settings(db: DbDep, user: UsersManageDep) -> IdentitySettingsOut:
    del user
    return _identity_out(db, identity.resolve(db))


@router.post("/identity/test", response_model=ConnectionTestOut)
def test_identity_settings(body: IdentitySettingsIn, db: DbDep, http: IdpHttpDep, user: UsersManageDep) -> ConnectionTestOut:
    """Checks the IdP these settings name, without saving them."""
    del user
    if body.auth_mode != "oidc":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Only single sign-on settings can be tested")
    config = _candidate(db, body)
    return _test_out(oidc.check_connection(config, http=http), config)


@router.put("/identity", response_model=IdentitySettingsOut)
def save_identity_settings(
    body: IdentitySettingsIn, db: DbDep, http: IdpHttpDep, request: Request, user: UsersManageDep
) -> IdentitySettingsOut:
    """Saves after testing the connection again: settings that can't reach
    the IdP would lock everyone out at their next sign-in. (An operator can
    undo a bad save: `python -m app.tenancy identity reset <slug>`.)"""
    if body.auth_mode == "local" and identity.local_sign_in_barred(db, current_tenant_id() or ""):
        # US38.12: its first admin came in by invitation to set up single sign-on.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="This organization signs in through its identity provider; local sign-in isn't available")
    config = _candidate(db, body)
    if body.auth_mode == "oidc":
        check = oidc.check_connection(config, http=http)
        if not check.ok:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="The connection test failed: " + "; ".join(check.problems)
            )
    before = identity.resolve(db)
    row = db.scalar(select(TenantIdentityModel))
    if row is None:
        row = TenantIdentityModel(id=new_id("tidp"))
        db.add(row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_at = utcnow().replace(tzinfo=None)
    row.updated_by = user.name
    db.flush()
    identity.forget(db)
    after = identity.resolve(db)
    changed = {
        key: {"from": getattr(before, key), "to": getattr(after, key)}
        for key in identity.EDITABLE if getattr(before, key) != getattr(after, key)
    }
    audit.record(db, actor=user, action="identity_settings_changed", object_type="tenant_identity", object_id=row.id,
                 request=request, detail={"previous_source": before.source, "changed": changed})
    return _identity_out(db, after)


def _token_out(row: ScimTokenModel) -> ScimTokenOut:
    return ScimTokenOut(id=row.id, label=row.label, created_at=row.created_at, created_by=row.created_by,
                        last_used_at=row.last_used_at, revoked_at=row.revoked_at)


@router.get("/scim", response_model=ScimSetup)
def scim_setup(db: DbDep, user: UsersManageDep) -> ScimSetup:
    del user
    config = identity.resolve(db)
    rows = db.scalars(select(ScimTokenModel).order_by(ScimTokenModel.created_at.desc()))
    return ScimSetup(base_url=config.scim_base_url, link_claim=config.scim_link_claim,
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


# ---- Epic 38, US38.8: support access ----------------------------------------------


def _grant_out(db, row: SupportGrantModel) -> SupportGrantOut:
    live = [r for r in db.scalars(select(SessionModel).where(SessionModel.support_grant_id == row.id))
            if sessions.is_alive(r)] if support.is_alive(row) else []
    return SupportGrantOut(
        id=row.id, access=row.access, operator_email=row.operator_email, reason=row.reason,
        granted_by=row.granted_by_name, created_at=row.created_at, expires_at=row.expires_at,
        revoked_at=row.revoked_at, revoked_by=row.revoked_by_name, active=support.is_alive(row),
        live_sessions=len(live),
    )


@router.get("/support-grants", response_model=list[SupportGrantOut])
def list_support_grants(db: DbDep, user: UsersManageDep) -> list[SupportGrantOut]:
    del user
    rows = db.scalars(select(SupportGrantModel).order_by(SupportGrantModel.created_at.desc()).limit(50))
    return [_grant_out(db, r) for r in rows]


@router.post("/support-grants", response_model=SupportGrantOut, status_code=201)
def grant_support(body: SupportGrantIn, db: DbDep, request: Request, user: UsersManageDep) -> SupportGrantOut:
    """Lets the hosting company's support in, until it expires or is revoked."""
    limit = get_settings().support_max_hours
    if body.hours > limit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Support access can last at most {limit} hours")
    if user.actor_type == "support":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Support can't grant itself access")
    now = utcnow().replace(tzinfo=None)
    email = (body.operator_email or "").strip().lower() or None
    row = SupportGrantModel(
        id=new_id("support"), access=body.access, operator_email=email, reason=body.reason.strip(),
        granted_by_id=user.id, granted_by_name=user.name, created_at=now, expires_at=now + timedelta(hours=body.hours),
    )
    db.add(row)
    db.flush()
    audit.record(db, actor=user, action="support_access_granted", object_type="support_grant", object_id=row.id,
                 request=request, detail={"access": row.access, "hours": body.hours, "operator_email": email,
                                          "reason": row.reason})
    return _grant_out(db, row)


@router.post("/support-grants/{grant_id}/revoke", response_model=SupportGrantOut)
def revoke_support(grant_id: str, db: DbDep, request: Request, user: UsersManageDep) -> SupportGrantOut:
    row = db.get(SupportGrantModel, grant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such support grant")
    ended = support.revoke(db, row, user.name)
    audit.record(db, actor=user, action="support_access_revoked", object_type="support_grant", object_id=row.id,
                 request=request, detail={"sessions_ended": ended})
    return _grant_out(db, row)


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
    return [_mapping_out(db, m) for m in directory.load(db, identity.resolve(db))]


@router.post("/group-mappings/preview", response_model=list[RoleChangeOut])
def preview_group_mappings(body: GroupMappings, db: DbDep, user: UsersManageDep) -> list[RoleChangeOut]:
    """US37.11: who would gain or lose which role. Changes nothing."""
    del user
    return _changes_out(db, directory.preview(db, _proposed(db, body)))


@router.put("/group-mappings", response_model=MappingsSaved)
def save_group_mappings(body: GroupMappings, db: DbDep, request: Request, user: UsersManageDep) -> MappingsSaved:
    """Replaces every mapping and applies them to everyone now."""
    proposed = _proposed(db, body)
    directory.load(db, identity.resolve(db))  # seed first, so saving counts as the deliberate change it is
    changes = directory.replace(db, proposed, actor=user, request=request)
    return MappingsSaved(mappings=[_mapping_out(db, m) for m in proposed], changes=_changes_out(db, changes))
