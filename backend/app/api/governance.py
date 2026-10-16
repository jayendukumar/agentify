"""Epic 37: users and their roles (US37.1), their sessions (US37.13), the
audit log (US37.6), and the organization's approval policy (US37.5)."""

import csv
import io
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.auth import audit, directory, sessions
from app.auth.views import user_out
from app.db import repository
from app.context.repository import get_organization_model
from app.db.models import AuditEventModel, ContextEntityModel, SessionModel, UserModel
from app.schemas.auth import SessionOut, UserOut, UserRolesUpdate
from app.schemas.governance import AuditEvent, GovernanceSettings

from .auth import session_out
from .deps import AuditReadDep, CurrentSessionDep, CurrentUserDep, DbDep, OrgAdminDep, UsersManageDep

router = APIRouter(prefix="/api", tags=["governance"])


def _get_user(db, user_id: str) -> UserModel:
    user = db.get(UserModel, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"user '{user_id}' not found")
    return user


def _org_admins(db) -> set[str]:
    return {
        u.id
        for u in repository.list_users(db)
        if u.active and any(r.role == "admin" and r.scope_type == "organization" for r in u.roles)
    }


@router.get("/users", response_model=list[UserOut])
def list_users(db: DbDep, user: UsersManageDep) -> list[UserOut]:
    del user
    return [
        user_out(db, u).model_copy(update={
            "active_sessions": len(sessions.active_sessions(db, u.id)), "groups": directory.group_labels(db, u),
        })
        for u in repository.list_users(db)
    ]


@router.put("/users/{user_id}/roles", response_model=UserOut)
def set_user_roles(user_id: str, body: UserRolesUpdate, db: DbDep, request: Request, user: UsersManageDep) -> UserOut:
    target = _get_user(db, user_id)
    if target.actor_type == "support":
        # Epic 38, US38.8: a support user's roles come from the support grant.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Support users get their roles from the support grant -- change the grant instead")
    for grant in body.roles:
        if grant.scope_type == "business_unit":
            unit = db.get(ContextEntityModel, grant.scope_id)
            if unit is None or unit.kind != "business_unit":
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown business unit '{grant.scope_id}'")
        elif grant.scope_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="An organization-wide role has no scope id")
    before = sorted(f"{r.role}@{r.scope_type}:{r.scope_id}" for r in target.roles)
    keeps_admin = any(g.role == "admin" and g.scope_type == "organization" for g in body.roles)
    if not keeps_admin and _org_admins(db) == {target.id}:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This is the last organization admin -- grant admin to someone else first")
    repository.set_admin_roles(
        db, target, [(g.role, g.scope_type, g.scope_id) for g in body.roles], granted_by=user.id
    )
    audit.record(
        db, actor=user, action="roles_changed", object_type="user", object_id=target.id, request=request,
        detail={"before": before, "after": sorted(f"{g.role}@{g.scope_type}:{g.scope_id}" for g in body.roles)},
    )
    return user_out(db, target)


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: str, db: DbDep, request: Request, user: UsersManageDep) -> UserOut:
    """Deactivated users keep their history but can't sign in; every
    session they hold ends now."""
    target = _get_user(db, user_id)
    if target.id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You can't deactivate yourself")
    if _org_admins(db) == {target.id}:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This is the last organization admin")
    target.active = False
    revoked = sessions.revoke_all(db, target.id, "user_deactivated")
    db.flush()
    audit.record(
        db, actor=user, action="user_deactivated", object_type="user", object_id=target.id, request=request,
        detail={"sessions_revoked": revoked},
    )
    return user_out(db, target)


@router.post("/users/{user_id}/activate", response_model=UserOut)
def activate_user(user_id: str, db: DbDep, request: Request, user: UsersManageDep) -> UserOut:
    target = _get_user(db, user_id)
    target.active = True
    db.flush()
    audit.record(db, actor=user, action="user_activated", object_type="user", object_id=target.id, request=request)
    return user_out(db, target)


@router.get("/users/{user_id}/sessions", response_model=list[SessionOut])
def list_user_sessions(user_id: str, db: DbDep, user: UsersManageDep, current: CurrentSessionDep) -> list[SessionOut]:
    """US37.13: where a user is signed in right now."""
    del user
    _get_user(db, user_id)
    return [session_out(s, current.id) for s in sessions.active_sessions(db, user_id)]


@router.delete("/users/{user_id}/sessions/{session_id}", status_code=204)
def revoke_user_session(
    user_id: str, session_id: str, db: DbDep, request: Request, response: Response, user: UsersManageDep,
    current: CurrentSessionDep,
) -> None:
    """US37.13: ends one session; it stops working on its next request."""
    target = db.get(SessionModel, session_id)
    if target is None or target.user_id != user_id or not sessions.is_alive(target):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such active session")
    sessions.revoke(db, target, "revoked_by_admin")
    audit.record(
        db, actor=user, action="session_revoked", object_type="session", object_id=target.id, request=request,
        detail={"user_id": user_id, "by": "admin"},
    )
    if target.id == current.id:
        sessions.clear_cookies(response)


@router.post("/users/{user_id}/sessions/revoke-all")
def revoke_all_user_sessions(
    user_id: str, db: DbDep, request: Request, response: Response, user: UsersManageDep, current: CurrentSessionDep,
) -> dict[str, int]:
    """US37.13: "Sign out everywhere" -- for a lost laptop or an incident,
    without waiting for the IdP."""
    target = _get_user(db, user_id)
    revoked = sessions.revoke_all(db, target.id, "revoked_by_admin")
    audit.record(
        db, actor=user, action="sessions_revoked_all", object_type="user", object_id=target.id, request=request,
        detail={"sessions_revoked": revoked},
    )
    if target.id == current.user_id:
        sessions.clear_cookies(response)
    return {"revoked": revoked}


def _audit_query(
    actor_id: str | None, action: str | None, process_id: str | None, outcome: str | None,
    since: datetime | None, until: datetime | None,
):
    query = select(AuditEventModel)
    if actor_id:
        query = query.where(AuditEventModel.actor_id == actor_id)
    if action:
        query = query.where(AuditEventModel.action == action)
    if process_id:
        query = query.where(AuditEventModel.process_id == process_id)
    if outcome:
        query = query.where(AuditEventModel.outcome == outcome)
    if since:
        query = query.where(AuditEventModel.at >= since.replace(tzinfo=None))
    if until:
        query = query.where(AuditEventModel.at <= until.replace(tzinfo=None))
    return query.order_by(AuditEventModel.at.desc())


def _audit_event(row: AuditEventModel) -> AuditEvent:
    return AuditEvent(
        id=row.id, at=row.at, actor_type=row.actor_type, actor_id=row.actor_id, actor_name=row.actor_name,
        action=row.action, object_type=row.object_type, object_id=row.object_id, process_id=row.process_id,
        outcome=row.outcome, ip=row.ip, detail=row.detail or {},
    )


@router.get("/audit-events", response_model=list[AuditEvent])
def list_audit_events(
    db: DbDep,
    user: AuditReadDep,
    actor_id: str | None = None,
    action: str | None = None,
    process_id: str | None = None,
    outcome: str | None = Query(None, pattern="^(success|refused)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> list[AuditEvent]:
    del user
    rows = db.scalars(_audit_query(actor_id, action, process_id, outcome, since, until).limit(limit))
    return [_audit_event(r) for r in rows]


@router.get("/audit-events/export")
def export_audit_events(
    db: DbDep,
    user: AuditReadDep,
    actor_id: str | None = None,
    action: str | None = None,
    process_id: str | None = None,
    outcome: str | None = Query(None, pattern="^(success|refused)$"),
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    del user
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["at", "actor", "actor_id", "action", "object_type", "object_id", "process_id", "outcome", "ip", "detail"])
    for row in db.scalars(_audit_query(actor_id, action, process_id, outcome, since, until).limit(50_000)):
        writer.writerow([
            row.at.isoformat(), row.actor_name or "", row.actor_id or "", row.action, row.object_type or "",
            row.object_id or "", row.process_id or "", row.outcome, row.ip or "", row.detail or {},
        ])
    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-events.csv"'},
    )


@router.get("/organization/governance", response_model=GovernanceSettings)
def get_governance(db: DbDep, user: CurrentUserDep) -> GovernanceSettings:
    del user
    return GovernanceSettings.model_validate(get_organization_model(db).governance or {})


@router.put("/organization/governance", response_model=GovernanceSettings)
def set_governance(body: GovernanceSettings, db: DbDep, user: OrgAdminDep) -> GovernanceSettings:
    for unit_id in body.required_agent_approvals_by_unit:
        unit = db.get(ContextEntityModel, unit_id)
        if unit is None or unit.kind != "business_unit":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown business unit '{unit_id}'")
    del user
    get_organization_model(db).governance = body.model_dump()
    db.flush()
    return body
