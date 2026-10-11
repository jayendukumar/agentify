"""Epic 31, US31.7: federated stewardship and scoped access.

A user with no business-unit memberships is unrestricted -- the default
for a single-unit organization, and what every user was before this epic.
Once a user belongs to units:

- they see group-wide context plus their units' context, and nothing
  scoped to or owned by another unit (attribute-based: the entity's owner
  and its `applies_to.business_units` are the attributes);
- they may confirm, edit, or retire only entities owned by a unit they
  steward. Group-wide entities stay with unrestricted (group) stewards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ContextEntityModel, UserBusinessUnitModel, UserModel


@dataclass
class Access:
    unrestricted: bool
    units: set[str] = field(default_factory=set)
    steward_units: set[str] = field(default_factory=set)


def access_for(session: Session, user: UserModel) -> Access:
    rows = list(session.scalars(select(UserBusinessUnitModel).where(UserBusinessUnitModel.user_id == user.id)))
    if not rows:
        return Access(unrestricted=True)
    return Access(
        unrestricted=False,
        units={r.business_unit_id for r in rows},
        steward_units={r.business_unit_id for r in rows if r.steward},
    )


def can_see(access: Access, entity: ContextEntityModel) -> bool:
    if access.unrestricted:
        return True
    if entity.kind == "business_unit":
        return entity.id in access.units
    if entity.owner_business_unit_id and entity.owner_business_unit_id not in access.units:
        return False
    scoped = set((entity.applies_to or {}).get("business_units") or [])
    return not scoped or bool(scoped & access.units)


def can_steward(access: Access, entity: ContextEntityModel) -> bool:
    if access.unrestricted:
        return True
    return entity.owner_business_unit_id is not None and entity.owner_business_unit_id in access.steward_units


def require_visible(access: Access, entity: ContextEntityModel) -> None:
    if not can_see(access, entity):
        # 404 rather than 403: another unit's context isn't acknowledged to exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"context entity '{entity.id}' not found")


def require_steward(access: Access, entity: ContextEntityModel) -> None:
    require_visible(access, entity)
    if not can_steward(access, entity):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Only a steward of the business unit that owns this context can change it",
        )


def owner_for_new(access: Access, requested: str | None) -> str | None:
    """Which unit owns something a restricted user creates: the one they
    asked for if they steward it, else their only stewarded unit."""
    if access.unrestricted:
        return requested
    if requested is not None:
        if requested not in access.steward_units:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only add context to units you steward")
        return requested
    if len(access.steward_units) == 1:
        return next(iter(access.steward_units))
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail="Say which business unit owns this (owner_business_unit_id) -- you steward none or several",
    )


def require_unrestricted(access: Access) -> None:
    if not access.unrestricted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This needs group-level (unrestricted) access")
