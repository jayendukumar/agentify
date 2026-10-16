from typing import Annotated

import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import audit, oidc, sessions, support
from app.auth.permissions import ROLE_LABELS, ROLE_PERMISSIONS, Role
from app.auth.views import user_out
from app.config import get_settings
from app.db import repository
from app.db.models import ContextEntityModel, SessionModel, SupportGrantModel, UserModel
from app.schemas.auth import (
    AuthConfig,
    LoginRequest,
    LogoutResult,
    RoleOption,
    SessionOut,
    SignedInUser,
    SupportSessionInfo,
)
from app.tenancy.identity import IdentityConfig, get_identity

from .deps import CurrentSessionDep, DbDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
OidcDep = Annotated[oidc.OidcProvider, Depends(oidc.get_oidc_provider)]
# Epic 38, US38.6: how this tenant signs in.
IdentityDep = Annotated[IdentityConfig, Depends(get_identity)]


def signed_in_user(db: Session, user: UserModel, record: SessionModel) -> SignedInUser:
    grant = db.get(SupportGrantModel, record.support_grant_id) if record.support_grant_id else None
    return SignedInUser(
        **user_out(db, user).model_dump(),
        csrf_token=record.csrf_token,
        session_id=record.id,
        session_expires_at=record.expires_at,
        idle_timeout_minutes=get_settings().auth_idle_timeout_minutes,
        support=SupportSessionInfo(access=grant.access, expires_at=grant.expires_at, granted_by=grant.granted_by_name)
        if grant else None,
    )


def session_out(record: SessionModel, current_id: str | None = None) -> SessionOut:
    return SessionOut(
        id=record.id, created_at=record.created_at, last_used_at=record.last_used_at, expires_at=record.expires_at,
        ip=record.ip, user_agent=record.user_agent, current=record.id == current_id, auth_method=record.auth_method,
    )


@router.get("/config", response_model=AuthConfig)
def auth_config(config: IdentityDep) -> AuthConfig:
    """Epic 37: public -- the sign-in page needs it before anyone signs in.
    Epic 38: this tenant's sign-in ("none": single sign-on isn't set up)."""
    settings = get_settings()
    return AuthConfig(
        auth_mode=config.auth_mode,
        environment=settings.environment,
        roles=[
            RoleOption(value=role.value, label=ROLE_LABELS[role], permissions=sorted(p.value for p in ROLE_PERMISSIONS[role]))
            for role in Role
        ],
        sso_name=config.display_name if config.auth_mode == "oidc" else None,
        sso_login_url="/api/auth/oidc/login" if config.auth_mode == "oidc" else None,
    )


@router.post("/login", response_model=SignedInUser)
def login(body: LoginRequest, db: DbDep, request: Request, response: Response, config: IdentityDep) -> SignedInUser:
    """Local sign-in: a name and self-picked roles. Settings validation
    refuses AUTH_MODE=local in production (US37.14), so this endpoint never
    serves one."""
    if config.auth_mode != "local":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Local sign-in is disabled")
    scope_type, scope_id = "organization", ""
    if body.business_unit_id:
        unit = db.get(ContextEntityModel, body.business_unit_id)
        if unit is None or unit.kind != "business_unit":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown business unit '{body.business_unit_id}'")
        scope_type, scope_id = "business_unit", unit.id
    user = repository.get_or_create_user(
        db, name=body.name.strip(), roles=[(role, scope_type, scope_id) for role in body.roles]
    )
    if not user.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This account is deactivated")
    issued = sessions.start(db, user, request)
    audit.record(
        db, actor=user, action="sign_in", object_type="user", object_id=user.id, request=request,
        detail={
            "mode": "local", "roles": list(body.roles), "scope": f"{scope_type}:{scope_id}".rstrip(":"),
            "session_id": issued.session.id,
        },
    )
    sessions.set_cookies(response, issued)
    return signed_in_user(db, user, issued.session)


@router.post("/refresh", response_model=SignedInUser)
def refresh(
    db: DbDep, request: Request, response: Response, provider: OidcDep, config: IdentityDep,
    asg_refresh: Annotated[str | None, Cookie()] = None,
) -> SignedInUser | JSONResponse:
    """US37.12: swaps the refresh cookie for a new pair. No CSRF header:
    after a page reload the frontend doesn't have the token yet -- this
    is how it gets one. The cookie is SameSite=Lax, so another site can't
    send it, and this only rotates tokens the caller already holds.

    Refusals are returned rather than raised so the transaction commits:
    a reused refresh token revokes the session, and that must stick."""
    def confirm_with_idp(db_, record: SessionModel) -> bool:
        """US37.12: an SSO session lives only as long as the account does at
        the IdP. Its refresh token is refused once the user is disabled
        there or their IdP session ends."""
        if not record.idp_refresh_token:
            return True
        token = oidc.unseal(config, record.idp_refresh_token)
        if token is None:  # sealed under a since-rotated client secret
            return False
        try:
            tokens = provider.refresh(token)
        except oidc.IdpUnavailable as exc:
            # Availability over strictness: an IdP outage shouldn't sign
            # everyone out. Idle and absolute limits still apply.
            logger.warning("IdP unreachable while refreshing session %s; keeping it: %s", record.id, exc)
            return True
        if tokens is None:
            return False
        if tokens.get("refresh_token"):
            record.idp_refresh_token = oidc.seal(config, tokens["refresh_token"])
        if tokens.get("id_token"):
            record.idp_id_token = tokens["id_token"]
        return True

    confirm = confirm_with_idp if config.auth_mode == "oidc" else None
    result = sessions.refresh(db, asg_refresh, confirm) if asg_refresh else None
    outcome = result.outcome if result else sessions.RefreshOutcome.INVALID
    if outcome is sessions.RefreshOutcome.ISSUED:
        sessions.set_cookies(response, result.issued)
        return signed_in_user(db, result.user, result.session)
    if outcome is sessions.RefreshOutcome.RACED:
        return signed_in_user(db, result.user, result.session)
    if outcome is sessions.RefreshOutcome.REUSED:
        audit.record(
            db, actor=result.user, action="refresh_token_reuse", object_type="session", object_id=result.session.id,
            outcome="refused", request=request,
            detail={"reason": "A refresh token was used twice -- the session was revoked"},
        )
    refused = JSONResponse({"detail": "Session expired or invalid -- log in again"}, status.HTTP_401_UNAUTHORIZED)
    sessions.clear_cookies(refused)
    return refused


@router.post("/logout", response_model=LogoutResult)
def logout(
    db: DbDep, request: Request, response: Response, record: CurrentSessionDep, provider: OidcDep, config: IdentityDep
) -> LogoutResult:
    """Ends this session. After an SSO sign-in it also returns the IdP's
    logout URL, so the IdP session ends too -- otherwise the next "Sign in"
    would go straight back in without asking."""
    logout_url = None
    if record.auth_method == "oidc":
        logout_url = provider.end_session_url(record.idp_id_token, config.frontend_url.rstrip("/") + "/")
    sessions.revoke(db, record, "signed_out")
    audit.record(
        db, actor=record.user, action="sign_out", object_type="user", object_id=record.user_id, request=request,
        detail={"session_id": record.id, "idp_logout": logout_url is not None},
    )
    sessions.clear_cookies(response)
    return LogoutResult(logout_url=logout_url)


@router.get("/support/enter")
def support_enter(token: str, db: DbDep, request: Request, config: IdentityDep) -> Response:
    """Epic 38, US38.8: an operator arrives from the console with a one-time
    link (valid for a minute) and gets a support session in this tenant,
    under the grant the link was made for. Refusals redirect to the sign-in
    page and are audited here."""
    frontend = config.frontend_url.rstrip("/")
    try:
        entered = support.enter(db, token, request)
    except support.SupportRefused as exc:
        audit.record(db, actor=None, actor_name="operator (support)", action="support_session_started",
                     outcome="refused", request=request, detail={"reason": str(exc)})
        return RedirectResponse(f"{frontend}/?auth_error=support_refused", status.HTTP_302_FOUND)
    response = RedirectResponse(f"{frontend}/", status.HTTP_302_FOUND)
    sessions.set_cookies(response, entered.issued)
    return response


@router.get("/me", response_model=SignedInUser)
def me(db: DbDep, record: CurrentSessionDep) -> SignedInUser:
    return signed_in_user(db, record.user, record)


@router.get("/sessions", response_model=list[SessionOut])
def my_sessions(db: DbDep, record: CurrentSessionDep) -> list[SessionOut]:
    """US37.13: a user sees where they're signed in."""
    return [session_out(s, record.id) for s in sessions.active_sessions(db, record.user_id)]


@router.delete("/sessions/{session_id}", status_code=204)
def end_my_session(session_id: str, db: DbDep, request: Request, response: Response, record: CurrentSessionDep) -> None:
    """US37.13: a user ends one of their own sessions -- this one included,
    which signs them out here."""
    target = db.get(SessionModel, session_id)
    if target is None or target.user_id != record.user_id or not sessions.is_alive(target):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such active session")
    sessions.revoke(db, target, "revoked_by_user")
    audit.record(
        db, actor=record.user, action="session_revoked", object_type="session", object_id=target.id, request=request,
        detail={"user_id": record.user_id, "by": "self"},
    )
    if target.id == record.id:
        sessions.clear_cookies(response)
