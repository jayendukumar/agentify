from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.auth.permissions import ROLE_LABELS, ROLE_PERMISSIONS, Role
from app.auth.views import user_out
from app.config import get_settings
from app.db import repository
from app.db.models import ContextEntityModel, SessionModel, UserModel
from app.schemas.auth import AuthConfig, LoginRequest, RoleOption, SessionOut, SignedInUser

from .deps import CurrentSessionDep, DbDep

router = APIRouter(prefix="/api/auth", tags=["auth"])


def signed_in_user(db: Session, user: UserModel, record: SessionModel) -> SignedInUser:
    return SignedInUser(
        **user_out(db, user).model_dump(),
        csrf_token=record.csrf_token,
        session_id=record.id,
        session_expires_at=record.expires_at,
        idle_timeout_minutes=get_settings().auth_idle_timeout_minutes,
    )


def session_out(record: SessionModel, current_id: str | None = None) -> SessionOut:
    return SessionOut(
        id=record.id, created_at=record.created_at, last_used_at=record.last_used_at, expires_at=record.expires_at,
        ip=record.ip, user_agent=record.user_agent, current=record.id == current_id,
    )


@router.get("/config", response_model=AuthConfig)
def auth_config() -> AuthConfig:
    """Epic 37: public -- the sign-in page needs it before anyone signs in."""
    settings = get_settings()
    return AuthConfig(
        auth_mode=settings.auth_mode,
        environment=settings.environment,
        roles=[
            RoleOption(value=role.value, label=ROLE_LABELS[role], permissions=sorted(p.value for p in ROLE_PERMISSIONS[role]))
            for role in Role
        ],
    )


@router.post("/login", response_model=SignedInUser)
def login(body: LoginRequest, db: DbDep, request: Request, response: Response) -> SignedInUser:
    """Local sign-in: a name and self-picked roles. Settings validation
    refuses AUTH_MODE=local in production (US37.14), so this endpoint never
    serves one."""
    if get_settings().auth_mode != "local":
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
    db: DbDep, request: Request, response: Response, asg_refresh: Annotated[str | None, Cookie()] = None
) -> SignedInUser | JSONResponse:
    """US37.12: swaps the refresh cookie for a new pair. No CSRF header:
    after a page reload the frontend doesn't have the token yet -- this
    is how it gets one. The cookie is SameSite=Lax, so another site can't
    send it, and this only rotates tokens the caller already holds.

    Refusals are returned rather than raised so the transaction commits:
    a reused refresh token revokes the session, and that must stick."""
    result = sessions.refresh(db, asg_refresh) if asg_refresh else None
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


@router.post("/logout", status_code=204)
def logout(db: DbDep, request: Request, response: Response, record: CurrentSessionDep) -> None:
    sessions.revoke(db, record, "signed_out")
    audit.record(
        db, actor=record.user, action="sign_out", object_type="user", object_id=record.user_id, request=request,
        detail={"session_id": record.id},
    )
    sessions.clear_cookies(response)


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
