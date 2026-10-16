"""Epic 38, US38.12: handing a new tenant to its first admin.

An operator creates the tenant in the console with one of:

- **The first admin's email.** A one-time link, valid for 24 hours, lets
  them in as Admin on the tenant's own host, to set up single sign-on on
  the Directory page. The tenant never offers local sign-in (its
  `created_via` is `console`), so the link is the only way in until SSO
  works.
- **An IdP setup.** The tenant's identity provider is saved, and its admins
  group is mapped to Admin, so the first admin simply signs in.

A link can only be made while the tenant has neither single sign-on nor an
active admin, and is checked again when used: otherwise an operator could
invite themselves into any tenant, and US38.8 promises no standing access.

Everything here touching the tenant's rows runs as the tenant (app role,
row-level security), in short sessions, as the console does for usage
counts. Each step is audited in the tenant's own log; the console records
the same in the platform log.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import audit, sessions
from app.config import get_settings
from app.db.models import (
    GroupRoleMappingModel,
    TenantIdentityModel,
    TenantInvitationModel,
    UserModel,
    UserRoleModel,
)
from app.db.session import get_session_factory
from app.ids import new_id, utcnow

from . import identity
from .context import tenant_scope

INVITATION_LIFETIME = timedelta(hours=24)
CONSOLE = "console"


class OnboardingError(ValueError):
    pass


def _now() -> datetime:
    return utcnow().replace(tzinfo=None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _has_sso(db: Session) -> bool:
    return db.scalar(select(TenantIdentityModel.id).where(TenantIdentityModel.auth_mode == "oidc")) is not None


def _has_admin(db: Session) -> bool:
    return db.scalar(
        select(func.count()).select_from(UserRoleModel).join(UserModel, UserModel.id == UserRoleModel.user_id)
        .where(UserRoleModel.role == "admin", UserModel.active, UserModel.actor_type == "user")
    ) > 0


def _check_open(db: Session) -> None:
    if _has_sso(db):
        raise OnboardingError("This organization signs in through its own identity provider: its admins add people")
    if _has_admin(db):
        raise OnboardingError("This organization already has an admin")


def _record(db: Session, action: str, operator_name: str, detail: dict) -> None:
    audit.record(db, actor=None, actor_name=f"{operator_name} (operator)", action=action, actor_type="operator",
                 detail=detail)


# ---- the console's side (as the tenant) --------------------------------------------


def record_created(tenant_id: str, *, operator_name: str, detail: dict) -> None:
    with tenant_scope(tenant_id), get_session_factory()() as db, db.begin():
        _record(db, "tenant_created", operator_name, detail)


def invite(tenant_id: str, email: str, *, operator_name: str) -> str:
    """A new link for the first admin; any earlier unused one stops working.
    Returns the token, which is shown once and never stored."""
    email = email.strip()
    if "@" not in email or len(email) > 254:
        raise OnboardingError("Give the first admin's email address")
    token = secrets.token_urlsafe(32)
    now = _now()
    with tenant_scope(tenant_id), get_session_factory()() as db, db.begin():
        _check_open(db)
        db.execute(delete(TenantInvitationModel).where(TenantInvitationModel.redeemed_at.is_(None)))
        db.add(TenantInvitationModel(id=new_id("tinv"), token_hash=_hash(token), email=email,
                                     invited_by=operator_name, created_at=now, expires_at=now + INVITATION_LIFETIME))
        _record(db, "first_admin_invited", operator_name,
                {"email": email, "expires_at": (now + INVITATION_LIFETIME).isoformat()})
    return token


def configure_idp(tenant_id: str, slug: str, values: dict, *, admin_group: str, operator_name: str) -> None:
    """Saves the tenant's identity provider and maps its admins group to
    Admin. Not tested against the IdP here: the IdP may not trust the
    tenant's host yet; the Directory page tests it."""
    if not admin_group.strip():
        raise OnboardingError("Name the IdP group whose members are the organization's admins")
    settings = get_settings()
    try:
        identity.validate(values, slug, settings)
    except identity.IdentityError as exc:
        raise OnboardingError(str(exc)) from exc
    with tenant_scope(tenant_id), get_session_factory()() as db, db.begin():
        row = db.scalar(select(TenantIdentityModel))
        if row is None:
            row = TenantIdentityModel(id=new_id("tidp"))
            db.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = _now()
        row.updated_by = f"{operator_name} (operator)"
        if not db.scalar(select(func.count()).select_from(GroupRoleMappingModel)):
            db.add(GroupRoleMappingModel(id=new_id("gmap"), group=admin_group.strip(), role="admin",
                                         created_by=f"{operator_name} (operator)"))
        _record(db, "identity_settings_changed", operator_name,
                {"issuer": values["issuer"], "client_id": values["client_id"], "admin_group": admin_group.strip(),
                 "by": "operator console"})


@dataclass
class FirstAdmin:
    """What the console shows: how the tenant's first admin gets in."""

    state: str  # invited | expired | joined | sso | none
    email: str | None = None
    expires_at: datetime | None = None


def first_admin(tenant_id: str) -> FirstAdmin:
    with tenant_scope(tenant_id), get_session_factory()() as db:
        if _has_sso(db):
            return FirstAdmin("sso")
        latest = db.scalar(select(TenantInvitationModel).order_by(TenantInvitationModel.created_at.desc()).limit(1))
        if latest is None:
            return FirstAdmin("joined" if _has_admin(db) else "none")
        if latest.redeemed_at is not None:
            return FirstAdmin("joined", latest.email)
        state = "invited" if latest.expires_at > _now() else "expired"
        return FirstAdmin(state, latest.email, latest.expires_at)


# ---- the tenant's side -------------------------------------------------------------


class InvitationRefused(Exception):
    pass


def _unique_name(db: Session, name: str) -> str:
    taken = set(db.scalars(select(UserModel.name).where(UserModel.name.like(f"{name}%"))))
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def redeem(db: Session, token: str, request: Request) -> sessions.Issued:
    """The first admin follows the link on the tenant's host: they become
    an Admin user and are signed in. Used once; refused once the tenant has
    single sign-on or an admin, whatever the link says."""
    invitation = db.scalar(select(TenantInvitationModel).where(TenantInvitationModel.token_hash == _hash(token)))
    if invitation is None or invitation.redeemed_at is not None:
        raise InvitationRefused("Unknown or already used invitation")
    if invitation.expires_at <= _now():
        raise InvitationRefused("The invitation expired; ask the hosting company for a new one")
    try:
        _check_open(db)
    except OnboardingError as exc:
        raise InvitationRefused(str(exc)) from exc
    user = UserModel(id=new_id("user"), name=_unique_name(db, invitation.email), provisioned_by="invitation",
                     email=invitation.email)
    db.add(user)
    db.flush()
    db.add(UserRoleModel(user_id=user.id, role="admin", scope_type="organization", scope_id="", source="invitation",
                         granted_by=f"{invitation.invited_by} (operator)"))
    invitation.redeemed_at = _now()
    invitation.redeemed_by_user_id = user.id
    db.flush()
    db.expire(user)
    issued = sessions.start(db, user, request, auth_method="invitation")
    audit.record(db, actor=user, action="first_admin_joined", object_type="user", object_id=user.id, request=request,
                 detail={"email": invitation.email, "invited_by": invitation.invited_by})
    return issued


def is_console_tenant(db: Session, tenant_id: str) -> bool:
    from app.db.models import TenantModel

    return db.scalar(select(TenantModel.created_via).where(TenantModel.id == tenant_id)) == CONSOLE
