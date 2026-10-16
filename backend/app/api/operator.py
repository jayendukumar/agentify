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
from app.operators import auth, tenants
from app.schemas.operator import (
    HostKind,
    OperatorConfig,
    OperatorLogin,
    OperatorLogout,
    OperatorMe,
    PlatformAuditEvent,
    SupportEntry,
    SupportGrantForOperator,
    TenantCreate,
    TenantOut,
    TenantUsage,
)
from app.tenancy.hosts import normalize_host

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
    usage = None
    if with_usage and tenant.status != "deleting":
        u = tenants.usage(tenant.id)
        usage = TenantUsage(**u.__dict__)
    return TenantOut(id=tenant.id, slug=tenant.slug, display_name=tenant.display_name, status=tenant.status,
                     region=tenant.region, created_at=tenant.created_at, hosts=tenants.hosts(db, tenant.id),
                     usage=usage)


def _tenant(db: Session, tenant_id: str) -> TenantModel:
    tenant = db.get(TenantModel, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such tenant")
    return tenant


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(record: OperatorSession, db: PlatformDb) -> list[TenantOut]:
    del record
    return [_tenant_out(db, t) for t in db.scalars(select(TenantModel).order_by(TenantModel.slug))]


@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(body: TenantCreate, record: OperatorSession, db: PlatformDb, request: Request) -> TenantOut:
    """A tenant and its first host. Its first admin and IdP come with
    US38.12; until then `python -m app.tenancy identity set`."""
    try:
        tenant = tenants.create(db, slug=body.slug, display_name=body.display_name, host=body.host, region=body.region)
    except tenants.TenantError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    auth.record(db, operator=record.operator, action="tenant_created", request=request, tenant_id=tenant.id,
                detail={"slug": tenant.slug, "host": normalize_host(body.host), "region": tenant.region})
    db.flush()
    db.refresh(tenant)
    return _tenant_out(db, tenant, with_usage=False)


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


@router.get("/audit", response_model=list[PlatformAuditEvent])
def platform_audit(record: OperatorSession, db: PlatformDb, limit: int = 200) -> list[PlatformAuditEvent]:
    del record
    rows = db.scalars(select(PlatformAuditEventModel).order_by(PlatformAuditEventModel.occurred_at.desc())
                      .limit(min(limit, 1000)))
    return [PlatformAuditEvent(id=r.id, occurred_at=r.occurred_at, operator_name=r.operator_name, action=r.action,
                               outcome=r.outcome, tenant_id=r.tenant_id, detail=r.detail) for r in rows]
