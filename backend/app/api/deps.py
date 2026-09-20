from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import repository
from app.db.models import UserModel
from app.db.session import get_db
from app.llm import LLMClient, get_llm_client
from app.registry.base import RegistryConnector
from app.registry.manager import get_registries

LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
DbDep = Annotated[Session, Depends(get_db)]
# Epic 13: a FastAPI dependency (not a bare lru_cache call) specifically so
# tests can override it the same way they override get_llm_client -- the
# real LocalRegistryConnector can't simulate an unreachable/auth-error
# registry (US13.6), so those paths need a fake connector injected here.
RegistriesDep = Annotated[dict[str, RegistryConnector], Depends(get_registries)]

SESSION_COOKIE_NAME = "asg_session"


def get_current_user(
    db: DbDep, asg_session: Annotated[str | None, Cookie()] = None
) -> UserModel:
    if asg_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    user = repository.get_user_for_session(db, asg_session)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid -- log in again")
    return user


CurrentUserDep = Annotated[UserModel, Depends(get_current_user)]


def require_editor(user: CurrentUserDep) -> UserModel:
    if user.role != "editor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This action requires editor access")
    return user


EditorDep = Annotated[UserModel, Depends(require_editor)]
