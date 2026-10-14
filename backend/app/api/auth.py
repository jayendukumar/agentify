from fastapi import APIRouter, HTTPException, Request, Response, status

from app.auth import audit
from app.auth.permissions import ROLE_LABELS, ROLE_PERMISSIONS, Role
from app.auth.views import user_out
from app.config import get_settings
from app.db import repository
from app.db.models import ContextEntityModel
from app.schemas.auth import AuthConfig, LoginRequest, RoleOption, UserOut

from .deps import CurrentUserDep, DbDep, SESSION_COOKIE_NAME

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 30 days, not a session-length cookie -- this is a local/small-team tool
# (US9.9), not something that needs to re-prompt a login every browser
# restart. Epic 37's US37.12 replaces this with short, rotating sessions.
_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


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


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, db: DbDep, request: Request, response: Response) -> UserOut:
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
    session = repository.create_session(db, user.id)
    audit.record(
        db, actor=user, action="sign_in", object_type="user", object_id=user.id, request=request,
        detail={"mode": "local", "roles": list(body.roles), "scope": f"{scope_type}:{scope_id}".rstrip(":")},
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session.id,
        httponly=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE_SECONDS,
    )
    return user_out(db, user)


@router.post("/logout", status_code=204)
def logout(db: DbDep, request: Request, response: Response) -> None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        user = repository.get_user_for_session(db, session_id)
        if user is not None:
            audit.record(db, actor=user, action="sign_out", object_type="user", object_id=user.id, request=request)
        repository.delete_session(db, session_id)
    response.delete_cookie(SESSION_COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(db: DbDep, user: CurrentUserDep) -> UserOut:
    return user_out(db, user)
