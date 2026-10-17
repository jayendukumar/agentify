"""Epic 38, US38.8: the operator console's API (see app/operators).

Only reachable on the operator hosts; the tenant middleware answers 404
for `/api/operator/*` on any tenant's host."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import oidc, support
from app.config import get_settings
from app.db.models import OperatorLoginRequestModel, OperatorSessionModel, PlatformAuditEventModel, TenantModel
from app.db.session import get_platform_session_factory
from app.ids import utcnow
from app.operators import auth, tenants
from app.schemas.operator import (
    HostKind,
    OperatorConfig,
    OperatorLogin,
    OperatorLogout,
    OperatorMe,
    PlatformAuditEvent,
    DeletionCertificate,
    FirstAdminOut,
    InvitationCreate,
    LastExportOut,
    SupportEntry,
    SupportGrantForOperator,
    TenantCreate,
    TenantCreated,
    TenantErase,
    TenantIdp,
    TenantOut,
    TenantUsage,
)
from app.schemas.usage import TenantLimitsIn, TenantLimitsOut
from app.tenancy import identity, lifecycle, onboarding
from app.tenancy.hosts import normalize_host
from app.tenancy.limits import limits_of

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/operator", tags=["operator"])
host_router = APIRouter(tags=["operator"])
STATE_COOKIE = "asg_operator_state"
_STATE_PATH = "/api/operator/oidc"


@host_router.get("/api/host", response_model=HostKind)
def host_kind(request: Request) -> HostKind:
    """Public: tells the frontend whether to show a tenant's app or the
    operator console on this host."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        return HostKind(kind="tenant", name=tenant.display_name)
    return HostKind(kind="operator")


def get_platform_db() -> Iterator[Session]:
    factory = get_platform_session_factory()
    if factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="The operator console isn't configured")
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


PlatformDb = Annotated[Session, Depends(get_platform_db, scope="function")]
IdpHttp = Annotated[httpx.Client | None, Depends(oidc.get_idp_http)]


def get_operator_session(
    request: Request, db: PlatformDb, asg_operator: Annotated[str | None, Cookie()] = None
) -> OperatorSessionModel:
    record = auth.authenticate(db, asg_operator) if asg_operator else None
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not signed in to the operator console")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not auth.csrf_ok(record, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Missing or outdated CSRF token",
                            headers={"X-Auth-Error": "csrf"})
    return record


OperatorSession = Annotated[OperatorSessionModel, Depends(get_operator_session)]


def _host(request: Request) -> str:
    return normalize_host(request.headers.get("host", ""))


def _config(request: Request):
    return auth.identity_config(get_settings(), _host(request))


def _provider(request: Request, http: httpx.Client | None) -> oidc.OidcProvider:
    config = _config(request)
    return oidc.OidcProvider(config, http=http) if http is not None else oidc.provider_for(config)


def _me(record: OperatorSessionModel) -> OperatorMe:
    return OperatorMe(id=record.operator.id, name=record.operator.name, email=record.operator.email,
                      csrf_token=record.csrf_token, session_expires_at=record.expires_at,
                      auth_method=record.auth_method)


# ---- sign-in ----------------------------------------------------------------------------


@router.get("/config", response_model=OperatorConfig)
def operator_config(request: Request) -> OperatorConfig:
    settings = get_settings()
    mode = settings.operator_auth_mode
    return OperatorConfig(
        auth_mode=mode, environment=settings.environment,
        sso_login_url=f"{_config(request).api_url}/api/operator/oidc/login" if mode == "oidc" else None,
        region=settings.deployment_region, regions=settings.region_deployments,
    )


@router.post("/login", response_model=OperatorMe)
def operator_local_login(body: OperatorLogin, db: PlatformDb, request: Request, response: Response) -> OperatorMe:
    """Development only (OPERATOR_AUTH_MODE=local, refused in production)."""
    if get_settings().operator_auth_mode != "local":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Operators sign in with the platform's SSO")
    name = body.name.strip()
    operator = auth.operator_for(db, issuer=auth.LOCAL_ISSUER, subject=name.lower(), name=name,
                                 email=(body.email or "").strip().lower() or None)
    started = auth.start(db, operator, "local")
    auth.record(db, operator=operator, action="operator_sign_in", request=request, detail={"mode": "local"})
    auth.set_cookie(response, started)
    return _me(started.session)


@router.get("/oidc/login")
def operator_oidc_login(request: Request, db: PlatformDb, http: IdpHttp) -> Response:
    config = _config(request)
    if config.auth_mode != "oidc":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The platform's SSO isn't set up")
    current = auth.now()
    db.execute(delete(OperatorLoginRequestModel).where(
        OperatorLoginRequestModel.created_at < current - timedelta(minutes=10)))
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    try:
        url = _provider(request, http).authorization_url(state=state, nonce=nonce, code_verifier=verifier)
    except (oidc.OidcError, oidc.IdpUnavailable) as exc:
        logger.error("Operator sign-in can't start: %s", exc)
        return RedirectResponse(f"{config.frontend_url}/?auth_error=idp_unavailable", status.HTTP_302_FOUND)
    db.add(OperatorLoginRequestModel(state_hash=hashlib.sha256(state.encode()).hexdigest(), nonce=nonce,
                                     code_verifier=verifier, created_at=current))
    response = RedirectResponse(url, status.HTTP_302_FOUND)
    response.set_cookie(STATE_COOKIE, state, max_age=600, path=_STATE_PATH, httponly=True, samesite="lax",
                        secure=get_settings().cookie_secure)
    return response


@router.get("/oidc/callback")
def operator_oidc_callback(
    request: Request, db: PlatformDb, http: IdpHttp, code: str | None = None, state: str | None = None,
    error: str | None = None, asg_operator_state: Annotated[str | None, Cookie()] = None,
) -> Response:
    """Every refusal is returned, not raised, so its audit event commits."""
    config = _config(request)
    if config.auth_mode != "oidc":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="The platform's SSO isn't set up")

    def refuse(code_: str, reason: str, who: str | None = None) -> Response:
        auth.record(db, operator=None, operator_name=who or "unknown (SSO)", action="operator_sign_in",
                    outcome="refused", request=request, detail={"error": code_, "reason": reason})
        response = RedirectResponse(f"{config.frontend_url}/?auth_error={code_}", status.HTTP_302_FOUND)
        response.delete_cookie(STATE_COOKIE, path=_STATE_PATH)
        return response

    if error:
        return refuse("idp_error", f"The IdP returned {error}")
    if not code or not state or not asg_operator_state or not hmac.compare_digest(state, asg_operator_state):
        return refuse("state_mismatch", "The sign-in response doesn't belong to a sign-in started in this browser")
    pending = db.get(OperatorLoginRequestModel, hashlib.sha256(state.encode()).hexdigest())
    if pending is None:
        return refuse("expired", "Unknown or already used sign-in state")
    db.delete(pending)
    if oidc.login_request_expired(pending.created_at, auth.now()):
        return refuse("expired", "The sign-in took longer than ten minutes")
    provider = _provider(request, http)
    try:
        tokens = provider.exchange_code(code, pending.code_verifier)
        claims = provider.validate_id_token(tokens["id_token"], nonce=pending.nonce)
    except oidc.OidcError as exc:
        return refuse(exc.code, str(exc))
    except oidc.IdpUnavailable as exc:
        return refuse("idp_unavailable", str(exc))
    who = claims.get("email") or claims.get("preferred_username") or claims["sub"]
    if not provider.mfa_satisfied(claims):
        return refuse("mfa_required", "The IdP didn't report multi-factor authentication", who)
    if get_settings().operator_group not in oidc.group_names(claims, config.groups_claim):
        return refuse("not_an_operator", f"Not in the {get_settings().operator_group} group", who)
    operator = auth.operator_for(
        db, issuer=claims["iss"], subject=claims["sub"],
        name=claims.get("name") or claims.get("preferred_username") or who, email=claims.get("email"),
    )
    started = auth.start(db, operator, "oidc", tokens["id_token"])
    auth.record(db, operator=operator, action="operator_sign_in", request=request,
                detail={"mode": "oidc", "amr": claims.get("amr"), "acr": claims.get("acr")})
    response = RedirectResponse(f"{config.frontend_url}/", status.HTTP_302_FOUND)
    auth.set_cookie(response, started)
    response.delete_cookie(STATE_COOKIE, path=_STATE_PATH)
    return response


@router.get("/me", response_model=OperatorMe)
def operator_me(record: OperatorSession) -> OperatorMe:
    return _me(record)


@router.post("/logout", response_model=OperatorLogout)
def operator_logout(record: OperatorSession, db: PlatformDb, request: Request, response: Response,
                    http: IdpHttp) -> OperatorLogout:
    record.revoked_at = auth.now()
    auth.record(db, operator=record.operator, action="operator_sign_out", request=request)
    auth.clear_cookie(response)
    logout_url = None
    if record.auth_method == "oidc":
        try:
            logout_url = _provider(request, http).end_session_url(record.idp_id_token, _config(request).frontend_url + "/")
        except (oidc.OidcError, oidc.IdpUnavailable):
            logout_url = None
    return OperatorLogout(logout_url=logout_url)


# ---- tenants ------------------------------------------------------------------------


def _tenant_out(db: Session, tenant: TenantModel, with_usage: bool = True) -> TenantOut:
    usage = first = export = None
    if with_usage and tenant.status != "deleting":
        u = tenants.usage(tenant.id)
        usage = TenantUsage(**u.__dict__)
        first = FirstAdminOut(**onboarding.first_admin(tenant.id).__dict__)
    if with_usage:
        last = lifecycle.last_export(tenant.id)
        export = LastExportOut(**last.__dict__) if last is not None else None
    effective = limits_of(tenant)
    return TenantOut(id=tenant.id, slug=tenant.slug, display_name=tenant.display_name, status=tenant.status,
                     region=tenant.region, created_at=tenant.created_at, hosts=tenants.hosts(db, tenant.id),
                     usage=usage, created_via=tenant.created_via, first_admin=first,
                     suspended_at=tenant.suspended_at, delete_after=tenant.delete_after, last_export=export,
                     dedicated_database=bool(tenant.database_ref),
                     limits=TenantLimitsIn(**{name: getattr(tenant, name) for name in tenants.LIMIT_FIELDS}),
                     effective_limits=TenantLimitsOut(**{name: getattr(effective, name)
                                                         for name in tenants.LIMIT_FIELDS}))


def _tenant(db: Session, tenant_id: str) -> TenantModel:
    tenant = db.get(TenantModel, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such tenant")
    return tenant


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(record: OperatorSession, db: PlatformDb) -> list[TenantOut]:
    del record
    return [_tenant_out(db, t) for t in db.scalars(select(TenantModel).order_by(TenantModel.slug))]


def _idp_values(idp: TenantIdp) -> dict:
    return {
        "auth_mode": "oidc", "issuer": idp.issuer.strip(), "discovery_url": idp.discovery_url.strip(),
        "client_id": idp.client_id.strip(), "client_secret_ref": idp.client_secret_ref.strip(),
        "display_name": idp.display_name, "scopes": "openid profile email", "acr_values": idp.acr_values,
        "groups_claim": "groups", "jit_provisioning": True, "require_mfa": True,
        "mfa_amr_values": idp.mfa_amr_values, "mfa_acr_values": idp.mfa_acr_values, "scim_link_claim": "sub",
    }


@router.post("/tenants", response_model=TenantCreated, status_code=201)
def create_tenant(body: TenantCreate, record: OperatorSession, db: PlatformDb, request: Request) -> TenantCreated:
    """US38.12: a tenant, its first host, and how its first admin gets in --
    an invitation to their email, or the tenant's IdP with its admins group
    mapped to Admin. Audited here and in the new tenant's own log."""
    if (body.first_admin_email is None) == (body.idp is None):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Say how the first admin gets in: their email for an invitation, or the IdP setup")
    operator = record.operator
    if body.idp is not None:  # checked before anything is created
        try:
            identity.validate(_idp_values(body.idp), body.slug.strip().lower(), get_settings())
        except identity.IdentityError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        if not body.idp.admin_group.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Name the IdP group whose members are its admins")
    elif "@" not in (body.first_admin_email or ""):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Give the first admin's email address")
    try:
        tenant = tenants.create(db, slug=body.slug, display_name=body.display_name, host=body.host, region=body.region)
    except tenants.TenantError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    first_admin = {"idp": body.idp.issuer, "admin_group": body.idp.admin_group} if body.idp else {
        "email": body.first_admin_email}
    auth.record(db, operator=operator, action="tenant_created", request=request, tenant_id=tenant.id,
                detail={"slug": tenant.slug, "host": normalize_host(body.host), "region": tenant.region,
                        "first_admin": first_admin})
    # The tenant's own rows reference its `tenants` row, so that commits first.
    db.commit()
    onboarding.record_created(tenant.id, operator_name=operator.name,
                              detail={"slug": tenant.slug, "host": normalize_host(body.host)})
    created = TenantCreated(tenant=_tenant_out(db, tenant, with_usage=False))
    try:
        if body.idp is not None:
            onboarding.configure_idp(tenant.id, tenant.slug, _idp_values(body.idp), admin_group=body.idp.admin_group,
                                     operator_name=operator.name)
            auth.record(db, operator=operator, action="tenant_idp_configured", request=request, tenant_id=tenant.id,
                        detail={"slug": tenant.slug, **first_admin})
        else:
            token = onboarding.invite(tenant.id, body.first_admin_email or "", operator_name=operator.name)
            created.invitation_url = tenants.invitation_url(tenants.hosts(db, tenant.id), token)
            created.invitation_expires_at = utcnow().replace(tzinfo=None) + onboarding.INVITATION_LIFETIME
            auth.record(db, operator=operator, action="first_admin_invited", request=request, tenant_id=tenant.id,
                        detail={"slug": tenant.slug, **first_admin})
    except onboarding.OnboardingError as exc:
        # The tenant exists; the operator can send an invitation from the list.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Created {tenant.slug}, but: {exc}") from exc
    return created


@router.post("/tenants/{tenant_id}/invitation", response_model=TenantCreated)
def invite_first_admin(tenant_id: str, body: InvitationCreate, record: OperatorSession, db: PlatformDb,
                       request: Request) -> TenantCreated:
    """US38.12: a new one-time link for the first admin (the earlier one
    stops working). Only while the tenant has no single sign-on and no admin."""
    tenant = _tenant(db, tenant_id)
    if tenant.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"The tenant is {tenant.status}")
    try:
        token = onboarding.invite(tenant.id, body.email, operator_name=record.operator.name)
    except onboarding.OnboardingError as exc:
        auth.record(db, operator=record.operator, action="first_admin_invited", outcome="refused", request=request,
                    tenant_id=tenant.id, detail={"email": body.email, "reason": str(exc)})
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    auth.record(db, operator=record.operator, action="first_admin_invited", request=request, tenant_id=tenant.id,
                detail={"slug": tenant.slug, "email": body.email})
    return TenantCreated(
        tenant=_tenant_out(db, tenant), invitation_url=tenants.invitation_url(tenants.hosts(db, tenant.id), token),
        invitation_expires_at=utcnow().replace(tzinfo=None) + onboarding.INVITATION_LIFETIME,
    )


@router.post("/tenants/{tenant_id}/erase", response_model=DeletionCertificate)
def erase_tenant(tenant_id: str, body: TenantErase, record: OperatorSession, db: PlatformDb,
                 request: Request) -> DeletionCertificate:
    """US38.13: erases a deleted tenant's data once its waiting period is
    over -- every tenant row, its files and its secrets -- and keeps a
    deletion certificate in the platform log."""
    tenant = _tenant(db, tenant_id)
    if body.confirm_slug.strip() != tenant.slug:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Type the tenant's slug ({tenant.slug}) to confirm")
    try:
        lifecycle.check_erasable(tenant)
    except lifecycle.LifecycleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    try:
        cert = tenants.erase(db, tenant, operator_name=record.operator.name)
    except tenants.TenantError as exc:
        db.rollback()
        auth.record(db, operator=record.operator, action="tenant_erased", outcome="failed", request=request,
                    tenant_id=tenant_id, detail={"slug": tenant.slug, "reason": str(exc)})
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    event = auth.record(db, operator=record.operator, action="tenant_erased", request=request, tenant_id=tenant_id,
                        detail=cert)
    db.flush()
    db.refresh(event)
    return DeletionCertificate(id=event.id, occurred_at=event.occurred_at, operator_name=event.operator_name,
                               tenant_id=tenant_id, certificate=cert)


@router.post("/tenants/{tenant_id}/{action}", response_model=TenantOut)
def change_tenant_status(tenant_id: str, action: str, record: OperatorSession, db: PlatformDb,
                         request: Request) -> TenantOut:
    if action not in tenants.TRANSITIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    tenant = _tenant(db, tenant_id)
    try:
        before = tenants.change_status(db, tenant, action)
    except tenants.TenantError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    auth.record(db, operator=record.operator, action=f"tenant_{action}", request=request, tenant_id=tenant.id,
                detail={"slug": tenant.slug, "from": before, "to": tenant.status})
    return _tenant_out(db, tenant, with_usage=False)


@router.put("/tenants/{tenant_id}/limits", response_model=TenantOut)
def set_tenant_limits(tenant_id: str, body: TenantLimitsIn, record: OperatorSession, db: PlatformDb,
                      request: Request) -> TenantOut:
    """US38.9: concurrent jobs, LLM requests per minute, monthly LLM budget.
    Empty means the deployment's default."""
    tenant = _tenant(db, tenant_id)
    before = tenants.set_limits(tenant, body.model_dump())
    auth.record(db, operator=record.operator, action="tenant_limits_changed", request=request, tenant_id=tenant.id,
                detail={"slug": tenant.slug, "from": before, "to": body.model_dump()})
    db.flush()
    return _tenant_out(db, tenant, with_usage=False)


# ---- support access -------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/support-grants", response_model=list[SupportGrantForOperator])
def tenant_support_grants(tenant_id: str, record: OperatorSession, db: PlatformDb) -> list[SupportGrantForOperator]:
    tenant = _tenant(db, tenant_id)
    if tenant.status != "active":
        return []
    return [
        SupportGrantForOperator(id=g.id, access=g.access, reason=g.reason, granted_by=g.granted_by_name,
                                expires_at=g.expires_at)
        for g in tenants.grants_for(tenant.id, record.operator.email)
    ]


@router.post("/tenants/{tenant_id}/support-grants/{grant_id}/enter", response_model=SupportEntry)
def enter_tenant(tenant_id: str, grant_id: str, record: OperatorSession, db: PlatformDb,
                 request: Request) -> SupportEntry:
    """A one-time link (a minute) into the tenant's own host, under the grant."""
    tenant = _tenant(db, tenant_id)
    if tenant.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"The tenant is {tenant.status}")
    operator = record.operator
    try:
        grant, token = tenants.open_support(tenant.id, grant_id, operator_id=operator.id, operator_name=operator.name,
                                            operator_email=operator.email)
    except tenants.TenantError as exc:
        auth.record(db, operator=operator, action="support_session_opened", outcome="refused", request=request,
                    tenant_id=tenant.id, detail={"grant_id": grant_id, "reason": str(exc)})
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    auth.record(db, operator=operator, action="support_session_opened", request=request, tenant_id=tenant.id,
                detail={"slug": tenant.slug, "grant_id": grant.id, "access": grant.access})
    return SupportEntry(url=tenants.entry_url(tenants.hosts(db, tenant.id), token),
                        expires_in_seconds=int(support.ENTRY_LIFETIME.total_seconds()))


@router.get("/deletion-certificates", response_model=list[DeletionCertificate])
def deletion_certificates(record: OperatorSession, db: PlatformDb) -> list[DeletionCertificate]:
    """US38.13: one per erased tenant."""
    del record
    rows = db.scalars(select(PlatformAuditEventModel).where(PlatformAuditEventModel.action == "tenant_erased",
                                                            PlatformAuditEventModel.outcome == "success")
                      .order_by(PlatformAuditEventModel.occurred_at.desc()))
    return [DeletionCertificate(id=r.id, occurred_at=r.occurred_at, operator_name=r.operator_name,
                                tenant_id=r.tenant_id, certificate=r.detail) for r in rows]


@router.get("/audit", response_model=list[PlatformAuditEvent])
def platform_audit(record: OperatorSession, db: PlatformDb, limit: int = 200) -> list[PlatformAuditEvent]:
    del record
    rows = db.scalars(select(PlatformAuditEventModel).order_by(PlatformAuditEventModel.occurred_at.desc())
                      .limit(min(limit, 1000)))
    return [PlatformAuditEvent(id=r.id, occurred_at=r.occurred_at, operator_name=r.operator_name, action=r.action,
                               outcome=r.outcome, tenant_id=r.tenant_id, detail=r.detail) for r in rows]
