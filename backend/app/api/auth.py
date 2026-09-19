from fastapi import APIRouter, Request, Response

from app.db import repository
from app.schemas.auth import LoginRequest, UserOut

from .deps import CurrentUserDep, DbDep, SESSION_COOKIE_NAME

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 30 days, not a session-length cookie -- this is a local/small-team tool
# (US9.9), not something that needs to re-prompt a login every browser
# restart.
_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, db: DbDep, response: Response) -> UserOut:
    user = repository.get_or_create_user(db, name=body.name, role=body.role)
    session = repository.create_session(db, user.id)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session.id,
        httponly=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE_SECONDS,
    )
    return user


@router.post("/logout", status_code=204)
def logout(db: DbDep, request: Request, response: Response) -> None:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        repository.delete_session(db, session_id)
    response.delete_cookie(SESSION_COOKIE_NAME)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUserDep) -> UserOut:
    return user
