from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Epic 37, US37.1: app/auth/permissions.py's Role, as an API type.
Role = Literal["analyst", "architect", "process_owner", "approver", "auditor", "admin"]
ScopeType = Literal["organization", "business_unit"]


class RoleGrant(BaseModel):
    role: Role
    scope_type: ScopeType = "organization"
    # "" for organization scope; a business unit's context entity id otherwise.
    scope_id: str = ""
    scope_name: str | None = None
    source: str | None = None


class LoginRequest(BaseModel):
    """Local sign-in only (AUTH_MODE=local, never production). No roles =
    a read-only member. `business_unit_id` scopes every picked role to that
    unit instead of the whole organization."""

    name: str = Field(min_length=1)
    roles: list[Role] = []
    business_unit_id: str | None = None


class UserOut(BaseModel):
    id: str
    name: str
    active: bool = True
    roles: list[RoleGrant] = []
    # Held at some scope -- for showing/hiding actions. The server still
    # checks the scope on every request.
    permissions: list[str] = []
    created_at: datetime
    # US37.13: only on the Users list.
    active_sessions: int | None = None


class SignedInUser(UserOut):
    """US37.12: what sign-in, refresh and /me return to the signed-in user
    themselves -- their CSRF token for writes, and when the session ends."""

    csrf_token: str
    session_id: str
    session_expires_at: datetime
    idle_timeout_minutes: int


class SessionOut(BaseModel):
    """US37.13: one live sign-in, for the user's own list and the admin's."""

    id: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    ip: str | None = None
    user_agent: str | None = None
    current: bool = False
    auth_method: str = "local"


class LogoutResult(BaseModel):
    """US37.7: after an SSO session, where to send the browser so the IdP
    ends its own session too (RP-initiated logout); null otherwise."""

    logout_url: str | None = None


class RoleOption(BaseModel):
    value: Role
    label: str
    permissions: list[str]


class AuthConfig(BaseModel):
    """Public: what the sign-in page needs before anyone is signed in."""

    auth_mode: str
    environment: str
    roles: list[RoleOption]
    # AUTH_MODE=oidc (US37.7): the button label and where it goes.
    sso_name: str | None = None
    sso_login_url: str | None = None


class UserRolesUpdate(BaseModel):
    roles: list[RoleGrant]
