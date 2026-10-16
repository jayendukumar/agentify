"""Epic 38, US38.8: support access -- the hosting company's staff inside a
tenant, only because its admin let them, for a set time.

- A tenant admin grants it (`support_grants`, a tenant row): read-only
  by default, optionally for one operator's email, ending at a set time.
- An operator opens it from the console, which (as the tenant) checks
  the grant and writes a one-time entry; the operator's browser redeems
  it within a minute on the tenant's own host (`enter`). That makes a
  tenant session for a support user -- the operator, as this tenant sees
  them -- whose roles come from the grant, never from the Users page.
- Every request in that session checks the grant is still alive, is
  refused if it writes under a read-only grant, and is written to the
  tenant's audit log with the operator's identity (`check`).

Operators never get a role that bypasses row-level security: a support
session is an ordinary tenant session, held to the same database rules.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.auth.permissions import Role
from app.db.models import (
    SessionModel,
    SupportEntryModel,
    SupportGrantModel,
    UserIdentityModel,
    UserModel,
    UserRoleModel,
)
from app.ids import new_id, utcnow

ACCESS_ROLES: dict[str, tuple[Role, ...]] = {
    # Sees everything an auditor sees, and the requests that would change
    # something are refused (`check`).
    "read": (Role.AUDITOR,),
    # Works on processes and agents. Never Admin (users, sign-in, support
    # grants) or Approver: support can't widen its own access or approve.
    "write": (Role.ANALYST, Role.ARCHITECT, Role.PROCESS_OWNER, Role.AUDITOR),
}
ENTRY_LIFETIME = timedelta(seconds=60)
OPERATOR_ISSUER = "axyntro:operators"
# Requests a read-only support session may still send.
_READ_ONLY_WRITES = frozenset({"/api/auth/logout"})


def _now() -> datetime:
    return utcnow().replace(tzinfo=None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_alive(grant: SupportGrantModel | None, now: datetime | None = None) -> bool:
    return grant is not None and grant.revoked_at is None and grant.expires_at > (now or _now())


def allows(grant: SupportGrantModel, operator_email: str | None) -> bool:
    return not grant.operator_email or grant.operator_email.lower() == (operator_email or "").lower()


def active_grants(db: Session) -> list[SupportGrantModel]:
    now = _now()
    rows = db.scalars(select(SupportGrantModel).order_by(SupportGrantModel.created_at.desc()))
    return [g for g in rows if is_alive(g, now)]


def revoke(db: Session, grant: SupportGrantModel, by: str) -> int:
    """Ends the grant and every support session under it, now."""
    if grant.revoked_at is None:
        grant.revoked_at = _now()
        grant.revoked_by_name = by
    ended = 0
    for record in db.scalars(select(SessionModel).where(SessionModel.support_grant_id == grant.id)):
        if sessions.is_alive(record):
            sessions.revoke(db, record, "support_grant_ended")
            ended += 1
    return ended


# ---- the console's side (run as the tenant) ----------------------------------------


def create_entry(db: Session, grant: SupportGrantModel, *, operator_id: str, operator_name: str,
                 operator_email: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(SupportEntryModel(
        token_hash=_hash(token), grant_id=grant.id, operator_id=operator_id, operator_name=operator_name,
        operator_email=operator_email, created_at=now, expires_at=now + ENTRY_LIFETIME,
    ))
    return token


# ---- the tenant's side ------------------------------------------------------------


@dataclass
class Entered:
    user: UserModel
    grant: SupportGrantModel
    issued: sessions.Issued
    operator_name: str


class SupportRefused(Exception):
    pass


def _support_user(db: Session, entry: SupportEntryModel) -> UserModel:
    """The operator as this tenant sees them: one user per operator, found
    again on their next support session, with no roles of its own."""
    link = db.scalar(select(UserIdentityModel).where(  # row-level security: this tenant's
        UserIdentityModel.issuer == OPERATOR_ISSUER, UserIdentityModel.subject == entry.operator_id))
    user = db.get(UserModel, link.user_id) if link is not None else None
    if user is None:
        user = UserModel(id=new_id("user"), name=_unique_name(db, f"{entry.operator_name} (support)"),
                         actor_type="support", provisioned_by="support", email=entry.operator_email)
        db.add(user)
        db.flush()
        db.add(UserIdentityModel(issuer=OPERATOR_ISSUER, subject=entry.operator_id, user_id=user.id,
                                 email=entry.operator_email, username=entry.operator_name))
    if not user.active:
        # An admin deactivated this operator here: revoking the grant isn't
        # the only way to keep them out.
        raise SupportRefused("This operator's support user is deactivated in this organization")
    return user


def _unique_name(db: Session, name: str) -> str:
    taken = set(db.scalars(select(UserModel.name).where(UserModel.name.like(f"{name}%"))))
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def enter(db: Session, token: str, request: Request) -> Entered:
    now = _now()
    entry = db.get(SupportEntryModel, _hash(token))
    if entry is None:
        raise SupportRefused("Unknown or already used support link")
    db.delete(entry)
    if entry.expires_at <= now:
        raise SupportRefused("The support link expired; open it again from the console")
    grant = db.get(SupportGrantModel, entry.grant_id)
    if not is_alive(grant, now) or not allows(grant, entry.operator_email):
        raise SupportRefused("Support access has ended or doesn't cover this operator")
    user = _support_user(db, entry)
    # The grant decides the roles, afresh each time.
    for role in db.scalars(select(UserRoleModel).where(UserRoleModel.user_id == user.id)):
        db.delete(role)
    db.flush()
    for role in ACCESS_ROLES[grant.access]:
        db.add(UserRoleModel(user_id=user.id, role=role.value, scope_type="organization", scope_id="",
                             source="support", granted_by=grant.granted_by_name))
    db.flush()
    db.expire(user)
    issued = sessions.start(db, user, request, auth_method="support", support_grant_id=grant.id)
    issued.session.expires_at = min(issued.session.expires_at, grant.expires_at)
    audit.record(
        db, actor=user, action="support_session_started", object_type="support_grant", object_id=grant.id,
        request=request, detail=_who(entry.operator_name, entry.operator_email, entry.operator_id, grant),
    )
    return Entered(user=user, grant=grant, issued=issued, operator_name=entry.operator_name)


def _who(name: str, email: str | None, operator_id: str, grant: SupportGrantModel) -> dict:
    return {"operator": name, "operator_email": email, "operator_id": operator_id, "grant_id": grant.id,
            "access": grant.access}


def check(db: Session, record: SessionModel, request: Request) -> None:
    """For every request in a support session: the grant must still be
    alive, a read-only grant refuses writes, and the request is audited
    in the tenant's log, refused or not."""
    grant = db.get(SupportGrantModel, record.support_grant_id)
    user = record.user
    detail = {"method": request.method, "path": request.url.path, "grant_id": record.support_grant_id,
              "access": grant.access if grant else None, "operator_email": user.email}
    if not is_alive(grant):
        sessions.revoke(db, record, "support_grant_ended")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Support access has ended")
    if (grant.access == "read" and request.method not in sessions.SAFE_METHODS
            and request.url.path not in _READ_ONLY_WRITES):
        audit.record_refusal(actor=user, action="support_request", reason="Read-only support access",
                             request=request, detail=detail)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Read-only support access: nothing can be changed")
    _record_request(user, request, detail)


def _record_request(user: UserModel, request: Request, detail: dict) -> None:
    """In its own transaction, so it's kept even if the request fails."""
    from app.db.session import get_session_factory

    event = audit._event(actor=user, action="support_request", object_type=None, object_id=None, process_id=None,
                         outcome="success", request=request, detail=detail)
    with get_session_factory()() as session, session.begin():
        session.add(event)
