"""SCIM Users and Groups on top of the product's own users (US37.10).

A SCIM user is a UserModel with provisioned_by "scim". The resource the
IdP sent is kept as it arrived (minus any password), so a GET returns
what was written, and the columns the product uses -- userName,
externalId, email, active, display name -- are derived from it.

Deactivating (active=false) or deleting a user revokes every session they
hold in the same transaction. A deleted user keeps their history
(contributions, approvals, audit); they just disappear from SCIM. Group
membership changes recompute the members' roles through the group
mappings (app/auth/directory.py).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import audit, directory, sessions
from app.tenancy import identity
from app.db.models import (
    DirectoryGroupMemberModel,
    DirectoryGroupModel,
    ScimTokenModel,
    UserIdentityModel,
    UserModel,
)
from app.ids import new_id, utcnow

from .protocol import ENTERPRISE_USER, GROUP_SCHEMA, PATCH_OP, USER_SCHEMA, ScimError, apply_patch, clean, get

MAX_PAGE = 200


def _now() -> datetime:
    return utcnow().replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _base(db: Session) -> str:
    return identity.resolve(db).scim_base_url


class Client:
    """Who is calling: the token, for the audit trail."""

    def __init__(self, token: ScimTokenModel):
        self.token = token

    @property
    def name(self) -> str:
        return f"SCIM ({self.token.label})"


def _audit(db: Session, client: Client, action: str, user: UserModel | None = None, **detail: Any) -> None:
    audit.record(
        db, actor=None, actor_name=client.name, actor_type="service", action=action,
        object_type="user" if user else detail.pop("object_type", None),
        object_id=user.id if user else detail.pop("object_id", None), detail=detail,
    )


# -- users ----------------------------------------------------------------------------


def user_resource(db: Session, user: UserModel) -> dict[str, Any]:
    profile = dict(user.scim_profile or {})
    groups = db.scalars(
        select(DirectoryGroupModel)
        .join(DirectoryGroupMemberModel, DirectoryGroupMemberModel.group_id == DirectoryGroupModel.id)
        .where(DirectoryGroupMemberModel.user_id == user.id)
        .order_by(DirectoryGroupModel.display_name)
    )
    schemas = [USER_SCHEMA] + ([ENTERPRISE_USER] if any(k.lower() == ENTERPRISE_USER.lower() for k in profile) else [])
    resource = {
        **profile,
        "schemas": schemas,
        "id": user.id,
        "userName": user.scim_user_name or get(profile, "userName"),
        "active": user.active,
        "groups": [{"value": g.id, "display": g.display_name, "$ref": f"{_base(db)}/Groups/{g.id}"} for g in groups],
        "meta": {"resourceType": "User", "created": _iso(user.created_at), "location": f"{_base(db)}/Users/{user.id}"},
    }
    if user.scim_external_id:
        resource["externalId"] = user.scim_external_id
    return resource


def _display_name(profile: dict[str, Any]) -> str:
    name = get(profile, "name") or {}
    parts = [get(name, "givenName"), get(name, "familyName")] if isinstance(name, dict) else []
    return (
        get(profile, "displayName")
        or (get(name, "formatted") if isinstance(name, dict) else None)
        or " ".join(p for p in parts if p)
        or get(profile, "userName")
    ).strip()


def _email(profile: dict[str, Any]) -> str | None:
    emails = [e for e in get(profile, "emails") or [] if isinstance(e, dict) and get(e, "value")]
    primary = next((e for e in emails if get(e, "primary") is True), emails[0] if emails else None)
    return get(primary, "value") if primary else None


def _unique_user_name(db: Session, wanted: str, user_id: str | None) -> str:
    candidates = [wanted, f"{wanted} (scim)", f"{wanted} ({new_id('u')})"]
    for candidate in candidates:
        holder = db.scalar(select(UserModel.id).where(UserModel.name == candidate))
        if holder is None or holder == user_id:
            return candidate
    return candidates[-1]


def _check_user_name_free(db: Session, user_name: str, user_id: str | None) -> None:
    holder = db.scalar(
        select(UserModel.id).where(
            func.lower(UserModel.scim_user_name) == user_name.lower(), UserModel.scim_deleted_at.is_(None)
        )
    )
    if holder is not None and holder != user_id:
        raise ScimError(409, f"A user with userName {user_name!r} already exists", "uniqueness")


def _write_user(db: Session, client: Client, user: UserModel, profile: dict[str, Any]) -> None:
    """Stores the resource and derives the columns from it."""
    user_name = get(profile, "userName")
    if not user_name or not isinstance(user_name, str):
        raise ScimError(400, "userName is required", "invalidValue")
    _check_user_name_free(db, user_name, user.id)
    was_active = user.active
    active = get(profile, "active")
    user.scim_profile = {k: v for k, v in profile.items() if k.lower() not in ("active", "username", "externalid")}
    user.scim_user_name = user_name
    user.scim_external_id = get(profile, "externalId") or None
    user.email = _email(profile)
    user.name = _unique_user_name(db, _display_name(profile), user.id)
    user.active = active if isinstance(active, bool) else user.active
    db.flush()
    if was_active and not user.active:
        revoked = sessions.revoke_all(db, user.id, "user_deactivated")
        _audit(db, client, "user_deactivated", user, sessions_revoked=revoked)
    elif not was_active and user.active:
        _audit(db, client, "user_activated", user)


def _find_user(db: Session, user_id: str) -> UserModel:
    user = db.get(UserModel, user_id)
    if user is None or user.provisioned_by != "scim" or user.scim_deleted_at is not None:
        raise ScimError(404, f"User {user_id} not found")
    return user


def get_user(db: Session, user_id: str) -> dict[str, Any]:
    return user_resource(db, _find_user(db, user_id))


def list_users(db: Session, filt: tuple[str, str] | None, start: int, count: int) -> tuple[list[dict], int]:
    query = select(UserModel).where(UserModel.provisioned_by == "scim", UserModel.scim_deleted_at.is_(None))
    if filt:
        attribute, value = filt
        column = {
            "username": func.lower(UserModel.scim_user_name),
            "externalid": UserModel.scim_external_id,
            "id": UserModel.id,
            "emails": func.lower(UserModel.email),
            "emails.value": func.lower(UserModel.email),
        }[attribute]
        query = query.where(column == (value.lower() if attribute in ("username", "emails", "emails.value") else value))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(UserModel.created_at, UserModel.id).offset(start - 1).limit(count))
    return [user_resource(db, u) for u in rows], total


def create_user(db: Session, client: Client, body: dict[str, Any]) -> dict[str, Any]:
    profile = clean(body)
    config = identity.resolve(db)
    external_id = get(profile, "externalId")
    user = None
    # Someone who already signed in over SSO (JIT) and is now being
    # provisioned: adopt their account instead of creating a second one.
    if external_id and config.scim_link_claim == "sub" and config.issuer:
        linked = db.get(UserIdentityModel, (config.issuer, str(external_id)))
        if linked is not None:
            user = db.get(UserModel, linked.user_id)
    adopted = user is not None
    if user is None:
        user = UserModel(id=new_id("user"), name=new_id("pending"), provisioned_by="scim")
        db.add(user)
    user.provisioned_by = "scim"
    if get(profile, "active") is None:
        profile["active"] = True
    _write_user(db, client, user, profile)
    _audit(db, client, "user_provisioned", user, user_name=user.scim_user_name, adopted_sso_user=adopted)
    directory.reapply(db, identity.resolve(db), [user], reason="provisioned", actor_name=client.name)
    return user_resource(db, user)


def replace_user(db: Session, client: Client, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user = _find_user(db, user_id)
    profile = clean(body)
    if get(profile, "active") is None:
        profile["active"] = user.active
    _write_user(db, client, user, profile)
    _audit(db, client, "user_updated", user, operation="replace")
    return user_resource(db, user)


def patch_user(db: Session, client: Client, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user = _find_user(db, user_id)
    current = {
        **(user.scim_profile or {}), "userName": user.scim_user_name, "active": user.active,
        **({"externalId": user.scim_external_id} if user.scim_external_id else {}),
    }
    _write_user(db, client, user, apply_patch(current, body))
    _audit(db, client, "user_updated", user, operation="patch")
    return user_resource(db, user)


def delete_user(db: Session, client: Client, user_id: str) -> None:
    """Gone from the IdP's view; kept here, inactive, with their history."""
    user = _find_user(db, user_id)
    groups = db.scalars(select(DirectoryGroupMemberModel).where(DirectoryGroupMemberModel.user_id == user.id)).all()
    for membership in groups:
        db.delete(membership)
    revoked = sessions.revoke_all(db, user.id, "user_deactivated")
    user.active = False
    user.scim_deleted_at = _now()
    user.scim_user_name = None
    db.flush()
    _audit(db, client, "user_deprovisioned", user, sessions_revoked=revoked)
    directory.reapply(db, identity.resolve(db), [user], reason="deprovisioned", actor_name=client.name)


# -- groups ---------------------------------------------------------------------------


def group_resource(group: DirectoryGroupModel, db: Session, *, members: bool = True) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "schemas": [GROUP_SCHEMA],
        "id": group.id,
        "displayName": group.display_name,
        "meta": {"resourceType": "Group", "created": _iso(group.created_at), "lastModified": _iso(group.updated_at),
                 "location": f"{_base(db)}/Groups/{group.id}"},
    }
    if group.external_id:
        resource["externalId"] = group.external_id
    if members:
        users = {u.id: u for u in db.scalars(select(UserModel).where(UserModel.id.in_([m.user_id for m in group.members])))}
        resource["members"] = [
            {"value": m.user_id, "display": users[m.user_id].name, "$ref": f"{_base(db)}/Users/{m.user_id}"}
            for m in group.members if m.user_id in users
        ]
    return resource


def _find_group(db: Session, group_id: str) -> DirectoryGroupModel:
    group = db.get(DirectoryGroupModel, group_id)
    if group is None:
        raise ScimError(404, f"Group {group_id} not found")
    return group


def get_group(db: Session, group_id: str, *, members: bool) -> dict[str, Any]:
    return group_resource(_find_group(db, group_id), db, members=members)


def list_groups(db: Session, filt: tuple[str, str] | None, start: int, count: int, *, members: bool) -> tuple[list[dict], int]:
    query = select(DirectoryGroupModel)
    if filt:
        attribute, value = filt
        column = {"displayname": func.lower(DirectoryGroupModel.display_name), "externalid": DirectoryGroupModel.external_id,
                  "id": DirectoryGroupModel.id}[attribute]
        query = query.where(column == (value.lower() if attribute == "displayname" else value))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(DirectoryGroupModel.display_name, DirectoryGroupModel.id).offset(start - 1).limit(count))
    return [group_resource(g, db, members=members) for g in rows], total


def _member_ids(db: Session, values: Any) -> list[str]:
    ids = []
    for item in values or []:
        user_id = item.get("value") if isinstance(item, dict) else item
        user = db.get(UserModel, str(user_id)) if user_id else None
        if user is None or user.scim_deleted_at is not None:
            raise ScimError(400, f"Unknown member {user_id!r}", "invalidValue")
        ids.append(user.id)
    return list(dict.fromkeys(ids))


def _set_members(group: DirectoryGroupModel, user_ids: list[str]) -> set[str]:
    """Returns the users whose membership changed."""
    before = {m.user_id for m in group.members}
    group.members[:] = [m for m in group.members if m.user_id in user_ids]
    for user_id in user_ids:
        if user_id not in before:
            group.members.append(DirectoryGroupMemberModel(user_id=user_id))
    return before ^ set(user_ids)


def _check_group_name_free(db: Session, name: str, group_id: str | None) -> None:
    holder = db.scalar(select(DirectoryGroupModel.id).where(func.lower(DirectoryGroupModel.display_name) == name.lower()))
    if holder is not None and holder != group_id:
        raise ScimError(409, f"A group named {name!r} already exists", "uniqueness")


def _reapply_members(db: Session, client: Client, group: DirectoryGroupModel, user_ids: set[str], reason: str) -> None:
    users = [u for u in (db.get(UserModel, uid) for uid in user_ids) if u is not None]
    directory.reapply(db, identity.resolve(db), users, reason=f"{reason}: {group.display_name}", actor_name=client.name)


def create_group(db: Session, client: Client, body: dict[str, Any]) -> dict[str, Any]:
    name = get(body, "displayName")
    if not name:
        raise ScimError(400, "displayName is required", "invalidValue")
    _check_group_name_free(db, name, None)
    group = DirectoryGroupModel(id=new_id("dgroup"), display_name=name, external_id=get(body, "externalId") or None)
    db.add(group)
    db.flush()
    changed = _set_members(group, _member_ids(db, get(body, "members")))
    db.flush()
    _audit(db, client, "group_provisioned", object_type="group", object_id=group.id, name=name, members=len(changed))
    _reapply_members(db, client, group, changed, "added to group")
    return group_resource(group, db)


def replace_group(db: Session, client: Client, group_id: str, body: dict[str, Any]) -> dict[str, Any]:
    group = _find_group(db, group_id)
    name = get(body, "displayName") or group.display_name
    _check_group_name_free(db, name, group.id)
    renamed = name != group.display_name or (get(body, "externalId") or None) != group.external_id
    group.display_name, group.external_id = name, get(body, "externalId") or None
    group.updated_at = _now()
    changed = _set_members(group, _member_ids(db, get(body, "members")))
    db.flush()
    _audit(db, client, "group_updated", object_type="group", object_id=group.id, operation="replace")
    # A rename can change which mappings match, so every member is recomputed.
    affected = changed | ({m.user_id for m in group.members} if renamed else set())
    _reapply_members(db, client, group, affected, "group changed")
    return group_resource(group, db)


_MEMBER_FILTER_PATH = re.compile(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', re.IGNORECASE)


def patch_group(db: Session, client: Client, group_id: str, body: dict[str, Any]) -> None:
    group = _find_group(db, group_id)
    if PATCH_OP not in (body.get("schemas") or []):
        raise ScimError(400, f"A PATCH body must declare {PATCH_OP}", "invalidSyntax")
    members = [m.user_id for m in group.members]
    name, external_id = group.display_name, group.external_id
    for operation in body.get("Operations") or body.get("operations") or []:
        op, path, value = str(operation.get("op", "")).lower(), operation.get("path"), operation.get("value")
        if op not in ("add", "replace", "remove"):
            raise ScimError(400, f"Unknown PATCH operation {operation.get('op')!r}", "invalidSyntax")
        updates: dict[str, Any] = {}
        if path is None:
            if op == "remove" or not isinstance(value, dict):
                raise ScimError(400, "Without a path, add or replace needs an object value", "invalidValue")
            updates = {k.lower(): v for k, v in value.items()}
        elif _MEMBER_FILTER_PATH.match(path):
            target = _MEMBER_FILTER_PATH.match(path).group(1)
            if op != "remove":
                raise ScimError(400, "Only remove is supported on members[value eq ...]", "invalidPath")
            members = [m for m in members if m != target]
            continue
        else:
            updates = {path.lower(): value}
        for key, item in updates.items():
            if key == "members":
                ids = _member_ids(db, item) if op != "remove" or item else []
                if op == "add":
                    members += [i for i in ids if i not in members]
                elif op == "remove":
                    members = [m for m in members if m not in ids] if item else []
                else:
                    members = ids
            elif key == "displayname":
                name = item if op != "remove" else name
            elif key == "externalid":
                external_id = None if op == "remove" else item
            elif key not in ("id", "schemas", "meta"):
                raise ScimError(400, f"Groups don't have a {key!r} attribute here", "invalidPath")
    _check_group_name_free(db, name, group.id)
    renamed = name != group.display_name or external_id != group.external_id
    group.display_name, group.external_id = name, external_id
    group.updated_at = _now()
    changed = _set_members(group, list(dict.fromkeys(members)))
    db.flush()
    _audit(db, client, "group_updated", object_type="group", object_id=group.id, operation="patch",
           members_changed=len(changed), renamed=renamed)
    affected = changed | ({m.user_id for m in group.members} if renamed else set())
    _reapply_members(db, client, group, affected, "group changed")


def delete_group(db: Session, client: Client, group_id: str) -> None:
    group = _find_group(db, group_id)
    members = {m.user_id for m in group.members}
    db.delete(group)
    db.flush()
    _audit(db, client, "group_deprovisioned", object_type="group", object_id=group_id, name=group.display_name)
    users = [u for u in (db.get(UserModel, uid) for uid in members) if u is not None]
    directory.reapply(db, identity.resolve(db), users, reason=f"group deleted: {group.display_name}", actor_name=client.name)
