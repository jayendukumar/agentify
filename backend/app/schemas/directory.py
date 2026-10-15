"""Epic 37, US37.10-37.11: the Directory page -- SCIM tokens and group
mappings."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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


# ---- Epic 38, US38.6: this tenant's identity provider ------------------------------


class IdentitySettingsIn(BaseModel):
    """What an admin edits. The client secret is a reference
    (`env:TENANT_<SLUG>_...`), never the secret itself."""

    auth_mode: Literal["local", "oidc"]
    issuer: str = ""
    discovery_url: str = ""
    client_id: str = ""
    client_secret_ref: str = ""
    display_name: str = Field(default="single sign-on", max_length=80)
    scopes: str = "openid profile email"
    acr_values: str = ""
    groups_claim: str = "groups"
    jit_provisioning: bool = True
    require_mfa: bool = True
    mfa_amr_values: list[str] = Field(default_factory=lambda: ["mfa", "otp", "hwk", "swk"])
    mfa_acr_values: list[str] = Field(default_factory=list)
    scim_link_claim: str = "sub"


class IdentitySettingsOut(IdentitySettingsIn):
    auth_mode: Literal["local", "oidc", "none"]  # type: ignore[assignment]
    # tenant (saved here) | environment (the first tenant's defaults) | unset
    source: str
    secret_configured: bool
    # Secret references must start with this (one tenant can't use another's).
    secret_prefix: str
    # What to register at the IdP: derived from this organization's host.
    redirect_uri: str
    post_logout_redirect_uri: str
    backchannel_logout_url: str
    scim_base_url: str
    updated_at: datetime | None = None
    updated_by: str | None = None


class ConnectionTestOut(BaseModel):
    ok: bool
    issuer: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    end_session_endpoint: str | None = None
    signing_keys: int = 0
    secret_configured: bool = False
    problems: list[str] = Field(default_factory=list)
