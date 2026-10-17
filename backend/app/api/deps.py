from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import audit, sessions, sod, support
from app.auth.permissions import Permission, has_permission, missing_permission_message
from app.db.models import ProcessModel, SessionModel, UserModel
from app.db.session import get_db
from app.llm import LLMClient, get_llm_client
from app.registry.base import RegistryConnector
from app.registry.manager import get_registries

LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
# scope="function": commit before the response is sent. With FastAPI's
# default request scope the commit ran after the client already had the
# reply, so an immediate follow-up request (e.g. right after login) could
# miss what the first one wrote.
DbDep = Annotated[Session, Depends(get_db, scope="function")]
# Epic 13: a FastAPI dependency (not a bare lru_cache call) specifically so
# tests can override it the same way they override get_llm_client -- the
# real LocalRegistryConnector can't simulate an unreachable/auth-error
# registry (US13.6), so those paths need a fake connector injected here.
RegistriesDep = Annotated[dict[str, RegistryConnector], Depends(get_registries)]

def get_current_session(
    request: Request, db: DbDep, asg_session: Annotated[str | None, Cookie()] = None
) -> SessionModel:
    """Epic 37, US37.12: the live session behind the session cookie. A 401
    tells the frontend to try /api/auth/refresh once before signing out.
    Writes (anything but GET/HEAD/OPTIONS) also need the session's CSRF
    token in a header; the 403 carries `X-Auth-Error: csrf` so the
    frontend can fetch the current token and retry."""
    if asg_session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
    record = sessions.authenticate(db, asg_session)
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid -- log in again")
    if request.method not in sessions.SAFE_METHODS and not sessions.csrf_ok(
        record, request.headers.get(sessions.CSRF_HEADER)
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Missing or outdated CSRF token -- reload the page and try again",
            headers={"X-Auth-Error": "csrf"},
        )
    if record.support_grant_id is not None:
        support.check(db, record, request)  # Epic 38, US38.8: grant alive, read-only, audited
    return record


CurrentSessionDep = Annotated[SessionModel, Depends(get_current_session)]


def get_current_user(record: CurrentSessionDep) -> UserModel:
    return record.user  # already loaded by authenticate(), so no extra query


CurrentUserDep = Annotated[UserModel, Depends(get_current_user)]


def require_process(process_id: str, db: DbDep) -> None:
    """Epic 38, US38.5: a process this tenant can't see is a 404 before
    the route does anything. Row-level security already hides the other
    tenant's rows, so a list would come back empty -- but a write could
    still store a row that points at the other tenant's process, because
    Postgres checks foreign keys without row-level security. Routers whose
    routes don't look the process up themselves declare this."""
    if db.get(ProcessModel, process_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Process '{process_id}' not found")


ProcessScope = Depends(require_process)


# Epic 37, US37.2: every write route declares the permission it needs. The
# guard checks it against the route's process (its business unit scope),
# and after the route succeeds records the audit event (US37.6) and, for
# edits to a process or an agent, a standard-tier contribution (US37.3)
# unless the route already recorded a more specific one. Scoped "function"
# so this runs before DbDep commits and before the response is sent.
_CONTRIBUTING: frozenset[Permission] = frozenset(
    {
        Permission.PROCESS_EDIT,
        Permission.AGENT_BUILD,
        Permission.AGENT_TEST,
        Permission.AGENT_RELEASE_CANDIDATE,
        Permission.RELEASE_CRITERIA_SET,
    }
)


def require(permission: Permission) -> Callable[..., Iterator[UserModel]]:
    def guard(request: Request, db: DbDep, user: CurrentUserDep) -> Iterator[UserModel]:
        params = dict(request.path_params)
        process_id = params.get("process_id")
        object_type, object_id = audit.target_of(params)
        action = getattr(request.scope.get("endpoint"), "__name__", request.url.path)
        if not has_permission(db, user, permission, process_id=process_id):
            reason = missing_permission_message(permission)
            audit.record_refusal(
                actor=user, action=action, reason=reason, object_type=object_type, object_id=object_id,
                process_id=process_id, request=request, detail={"permission": permission.value},
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=reason)
        yield user
        audit.record(
            db, actor=user, action=action, object_type=object_type, object_id=object_id, process_id=process_id,
            request=request, detail={"permission": permission.value, "path": params},
        )
        # A dataset or environment edit isn't a change to the process itself.
        contributes = object_type == "agent" or (object_type == "process" and permission is Permission.PROCESS_EDIT)
        if permission in _CONTRIBUTING and contributes and object_id:
            if not sod.already_recorded(db, object_type, object_id):
                sod.record_contribution(
                    db, object_type=object_type, object_id=object_id, process_id=process_id,
                    actor_id=user.id, action=action,
                )

    guard.required_permission = permission  # read by the route-guard completeness test
    return guard


def _dep(permission: Permission):
    return Annotated[UserModel, Depends(require(permission), scope="function")]


ProcessEditDep = _dep(Permission.PROCESS_EDIT)
ProcessFinalizeDep = _dep(Permission.PROCESS_FINALIZE)
ProcessApproveDep = _dep(Permission.PROCESS_APPROVE)
ProcessDeleteDep = _dep(Permission.PROCESS_DELETE)
BlueprintEditDep = _dep(Permission.BLUEPRINT_EDIT)
AgentBuildDep = _dep(Permission.AGENT_BUILD)
AgentTestDep = _dep(Permission.AGENT_TEST)
AgentReleaseCandidateDep = _dep(Permission.AGENT_RELEASE_CANDIDATE)
AgentApproveDep = _dep(Permission.AGENT_APPROVE)
ReleaseCriteriaDep = _dep(Permission.RELEASE_CRITERIA_SET)
AgentPublishDep = _dep(Permission.AGENT_PUBLISH)
AgentDeployDep = _dep(Permission.AGENT_DEPLOY)
RuntimeStopDep = _dep(Permission.RUNTIME_STOP)
ContextEditDep = _dep(Permission.CONTEXT_EDIT)
ContextConfirmDep = _dep(Permission.CONTEXT_CONFIRM)
CatalogEditDep = _dep(Permission.CATALOG_EDIT)
OrgAdminDep = _dep(Permission.ORG_ADMIN)
UsersManageDep = _dep(Permission.USERS_MANAGE)
AuditReadDep = _dep(Permission.AUDIT_READ)
