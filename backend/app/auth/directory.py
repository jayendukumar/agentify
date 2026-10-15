"""Epic 37, US37.11: IdP groups mapped to roles.

Every role a group grants is a UserRoleModel row with source "group",
recomputed here whenever a user's groups or the mappings change. A user's
groups are their SCIM group memberships if the IdP provisions them over
SCIM (US37.10), and otherwise the groups claim from their last SSO
sign-in (`users.idp_groups`). Keeping the last claim lets a mapping
change, and its preview, cover everyone at once rather than waiting for
each person to sign in again.

A mapping names a group as the IdP identifies it: a display name, a SCIM
external id or, for Entra ID's groups claim, an object id. Matching
ignores case. Unmapped groups grant nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.permissions import Role
from app.tenancy.identity import IdentityConfig
from app.db import repository
from app.db.models import (
    AuditEventModel,
    ContextEntityModel,
    DirectoryGroupMemberModel,
    DirectoryGroupModel,
    GroupRoleMappingModel,
    UserModel,
)
from app.ids import new_id

logger = logging.getLogger(__name__)

Grant = tuple[str, str, str]  # (role, scope_type, scope_id)
_SEEDED = "group_mappings_seeded"
_CHANGED = "group_mappings_changed"


@dataclass(frozen=True)
class Mapping:
    group: str
    role: str
    scope_type: str = "organization"
    scope_id: str = ""

    @property
    def grant(self) -> Grant:
        return (self.role, self.scope_type, self.scope_id)


class MappingError(ValueError):
    pass


def validate(db: Session, mappings: list[Mapping]) -> list[Mapping]:
    """Rejects unknown roles and business units, drops exact duplicates."""
    roles = {r.value for r in Role}
    for m in mappings:
        if not m.group.strip():
            raise MappingError("Every mapping needs a group")
        if m.role not in roles:
            raise MappingError(f"Unknown role '{m.role}'")
        if m.scope_type == "business_unit":
            unit = db.get(ContextEntityModel, m.scope_id)
            if unit is None or unit.kind != "business_unit":
                raise MappingError(f"Unknown business unit '{m.scope_id}'")
        elif m.scope_type != "organization" or m.scope_id:
            raise MappingError("A mapping is organization-wide or for one business unit")
    return list(dict.fromkeys(Mapping(m.group.strip(), m.role, m.scope_type, m.scope_id) for m in mappings))


def _bootstrap(db: Session, config: IdentityConfig) -> None:
    """OIDC_GROUP_ROLES seeds the table the first time mappings are needed,
    so a new deployment's first admin can sign in (the first tenant only:
    Epic 38, other tenants' config carries no seed). Only ever once: the
    audit log records the seeding (and any later save), so an admin who
    deliberately clears every mapping doesn't get them back."""
    if not config.seed_group_roles:
        return
    if db.scalar(select(func.count()).select_from(GroupRoleMappingModel)):
        return
    if db.scalar(select(AuditEventModel.id).where(AuditEventModel.action.in_([_SEEDED, _CHANGED])).limit(1)):
        return
    seeded, problems = [], []
    for group, specs in config.seed_group_roles.items():
        for spec in specs:
            role, _, unit_name = spec.partition("@")
            scope_type, scope_id = "organization", ""
            if unit_name:
                unit = db.scalar(
                    select(ContextEntityModel).where(
                        ContextEntityModel.kind == "business_unit", ContextEntityModel.name.ilike(unit_name.strip())
                    )
                )
                if unit is None:
                    problems.append(f"{group}: no business unit named {unit_name!r}")
                    continue
                scope_type, scope_id = "business_unit", unit.id
            if role not in {r.value for r in Role}:
                problems.append(f"{group}: unknown role {role!r}")
                continue
            seeded.append(Mapping(group, role, scope_type, scope_id))
    for m in dict.fromkeys(seeded):
        db.add(GroupRoleMappingModel(id=new_id("gmap"), group=m.group, role=m.role, scope_type=m.scope_type,
                                     scope_id=m.scope_id, created_by="OIDC_GROUP_ROLES"))
    audit.record(
        db, actor=None, actor_name="configuration (OIDC_GROUP_ROLES)", action=_SEEDED, object_type="group_mappings",
        detail={"mappings": [f"{m.group} -> {m.role}@{m.scope_type}:{m.scope_id}".rstrip(":") for m in seeded],
                "problems": problems},
    )
    db.flush()


def load(db: Session, config: IdentityConfig) -> list[Mapping]:
    _bootstrap(db, config)
    rows = db.scalars(select(GroupRoleMappingModel).order_by(GroupRoleMappingModel.group, GroupRoleMappingModel.role))
    return [Mapping(r.group, r.role, r.scope_type, r.scope_id) for r in rows]


def user_groups(db: Session, user: UserModel) -> list[str]:
    """Every identifier of every group the user is in."""
    if user.provisioned_by == "scim":
        groups = db.scalars(
            select(DirectoryGroupModel)
            .join(DirectoryGroupMemberModel, DirectoryGroupMemberModel.group_id == DirectoryGroupModel.id)
            .where(DirectoryGroupMemberModel.user_id == user.id)
        )
        return [name for g in groups for name in (g.display_name, g.external_id) if name]
    return list(user.idp_groups or [])


def group_labels(db: Session, user: UserModel) -> list[str]:
    """The user's groups as people read them (names, not ids)."""
    if user.provisioned_by == "scim":
        return sorted(db.scalars(
            select(DirectoryGroupModel.display_name)
            .join(DirectoryGroupMemberModel, DirectoryGroupMemberModel.group_id == DirectoryGroupModel.id)
            .where(DirectoryGroupMemberModel.user_id == user.id)
        ), key=str.casefold)
    return sorted(user.idp_groups or [], key=str.casefold)


def grants_for(groups: list[str], mappings: list[Mapping]) -> list[Grant]:
    held = {g.casefold() for g in groups}
    return sorted({m.grant for m in mappings if m.group.casefold() in held})


def _group_grants(user: UserModel) -> set[Grant]:
    return {(r.role, r.scope_type, r.scope_id) for r in user.roles if r.source == "group"}


@dataclass
class RoleChange:
    user_id: str
    user_name: str
    gained: list[Grant] = field(default_factory=list)
    lost: list[Grant] = field(default_factory=list)


def apply(db: Session, user: UserModel, mappings: list[Mapping]) -> RoleChange:
    """Brings one user's group-granted roles in line with their groups."""
    before = _group_grants(user)
    after = set(grants_for(user_groups(db, user), mappings))
    if before != after:
        repository.replace_group_roles(db, user, sorted(after))
    return RoleChange(user.id, user.name, sorted(after - before), sorted(before - after))


def _directory_users(db: Session) -> list[UserModel]:
    """Users whose groups are known: SCIM users, and SSO users with a
    recorded groups claim. Someone whose groups were never recorded (they
    last signed in before groups were kept) is left alone -- "unknown"
    isn't "no groups", and treating it so would strip their roles. Their
    roles are recomputed at their next sign-in."""
    return [
        u for u in repository.list_users(db)
        if u.scim_deleted_at is None and (u.provisioned_by == "scim" or u.idp_groups is not None)
    ]


def preview(db: Session, proposed: list[Mapping]) -> list[RoleChange]:
    """US37.11: who would gain or lose which role -- nothing is changed."""
    changes = []
    for user in _directory_users(db):
        before = _group_grants(user)
        after = set(grants_for(user_groups(db, user), proposed))
        if before != after:
            changes.append(RoleChange(user.id, user.name, sorted(after - before), sorted(before - after)))
    return changes


def replace(db: Session, proposed: list[Mapping], *, actor: UserModel, request=None) -> list[RoleChange]:
    """Saves the whole set of mappings and applies it to everyone."""
    db.execute(delete(GroupRoleMappingModel))
    for m in proposed:
        db.add(GroupRoleMappingModel(id=new_id("gmap"), group=m.group, role=m.role, scope_type=m.scope_type,
                                     scope_id=m.scope_id, created_by=actor.id))
    db.flush()
    changes = [c for c in (apply(db, u, proposed) for u in _directory_users(db)) if c.gained or c.lost]
    audit.record(
        db, actor=actor, action=_CHANGED, object_type="group_mappings", request=request,
        detail={"mappings": [f"{m.group} -> {m.role}@{m.scope_type}:{m.scope_id}".rstrip(":") for m in proposed],
                "users_changed": len(changes)},
    )
    return changes


def reapply(db: Session, config: IdentityConfig, users: list[UserModel], *, reason: str, actor_name: str) -> list[RoleChange]:
    """After group membership changed (SCIM): recompute these users, and
    audit any role that moved."""
    mappings = load(db, config)
    changes = []
    for user in dict.fromkeys(users):
        change = apply(db, user, mappings)
        if change.gained or change.lost:
            changes.append(change)
            audit.record(
                db, actor=None, actor_name=actor_name, actor_type="service", action="roles_changed",
                object_type="user", object_id=user.id,
                detail={"source": "group", "reason": reason,
                        "gained": [f"{r}@{t}:{i}".rstrip(":") for r, t, i in change.gained],
                        "lost": [f"{r}@{t}:{i}".rstrip(":") for r, t, i in change.lost]},
            )
    return changes


def known_groups(db: Session) -> list[dict]:
    """Groups an admin can map: those pushed over SCIM, and those seen in
    sign-in groups claims."""
    groups: dict[str, dict] = {}
    for g in db.scalars(select(DirectoryGroupModel).order_by(DirectoryGroupModel.display_name)):
        groups[g.display_name.casefold()] = {"name": g.display_name, "external_id": g.external_id, "source": "scim",
                                             "members": len(g.members)}
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for user in repository.list_users(db):
        for name in user.idp_groups or []:
            counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
            names.setdefault(name.casefold(), name)
    for key, count in counts.items():
        groups.setdefault(key, {"name": names[key], "external_id": None, "source": "sign-in", "members": count})
    return sorted(groups.values(), key=lambda g: g["name"].casefold())
