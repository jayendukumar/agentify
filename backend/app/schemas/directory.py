"""Epic 37, US37.10-37.11: the Directory page -- SCIM tokens and group
mappings."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .auth import Role, ScopeType


class ScimTokenOut(BaseModel):
    id: str
    label: str
    created_at: datetime
    created_by: str | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ScimTokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)


class ScimTokenIssued(ScimTokenOut):
    # Shown once; only its hash is kept.
    token: str


class ScimSetup(BaseModel):
    base_url: str
    link_claim: str
    tokens: list[ScimTokenOut]


class GroupMapping(BaseModel):
    group: str = Field(min_length=1)
    role: Role
    scope_type: ScopeType = "organization"
    scope_id: str = ""
    scope_name: str | None = None


class GroupMappings(BaseModel):
    mappings: list[GroupMapping]


class GrantOut(BaseModel):
    role: Role
    scope_type: ScopeType
    scope_id: str
    scope_name: str | None = None


class RoleChangeOut(BaseModel):
    user_id: str
    user_name: str
    gained: list[GrantOut]
    lost: list[GrantOut]


class MappingsSaved(BaseModel):
    mappings: list[GroupMapping]
    changes: list[RoleChangeOut]


class KnownGroup(BaseModel):
    name: str
    external_id: str | None = None
    source: str  # scim | sign-in
    members: int
