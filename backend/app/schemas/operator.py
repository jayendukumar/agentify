"""Epic 38, US38.8: the operator console's API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .usage import TenantLimitsIn, TenantLimitsOut


class HostKind(BaseModel):
    """Which app this host serves: a tenant's, or the operator console."""

    kind: Literal["tenant", "operator"]
    name: str | None = None


class OperatorConfig(BaseModel):
    auth_mode: Literal["local", "oidc", "none"]
    environment: str
    sso_login_url: str | None = None


class OperatorLogin(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str | None = None


class OperatorMe(BaseModel):
    id: str
    name: str
    email: str | None
    csrf_token: str
    session_expires_at: datetime
    auth_method: str


class OperatorLogout(BaseModel):
    logout_url: str | None = None


class TenantUsage(BaseModel):
    users: int
    processes: int
    jobs_waiting: int
    last_sign_in_at: datetime | None
    support_grants_active: int
    # US38.9: this month's LLM calls (all) and their estimated cost on the
    # platform's key (US38.11: not what a tenant spends on its own key).
    llm_calls_this_month: int
    llm_cost_this_month_usd: float


class TenantOut(BaseModel):
    id: str
    slug: str
    display_name: str
    status: str
    region: str | None
    created_at: datetime
    hosts: list[str]
    usage: TenantUsage | None = None
    # US38.9: the tenant's own limits (None: the deployment's default) and
    # what's in effect.
    limits: TenantLimitsIn
    effective_limits: TenantLimitsOut
    # US38.12: tenants an operator created; how their first admin gets in.
    created_via: str = "cli"
    first_admin: FirstAdminOut | None = None


class TenantIdp(BaseModel):
    """US38.12: the new tenant's identity provider, and the IdP group whose
    members are its admins. The client secret is a reference."""

    issuer: str
    discovery_url: str = ""
    client_id: str
    client_secret_ref: str = "secret://tenant/oidc_client_secret"
    display_name: str = "single sign-on"
    acr_values: str = ""
    mfa_amr_values: list[str] = Field(default_factory=lambda: ["mfa", "otp", "hwk", "swk"])
    mfa_acr_values: list[str] = Field(default_factory=list)
    admin_group: str


class TenantCreate(BaseModel):
    slug: str
    display_name: str = ""
    host: str
    region: str | None = None
    # US38.12: how its first admin gets in -- exactly one of these.
    first_admin_email: str | None = None
    idp: TenantIdp | None = None


class FirstAdminOut(BaseModel):
    # invited | expired | joined | sso | none
    state: str
    email: str | None = None
    expires_at: datetime | None = None


class TenantCreated(BaseModel):
    tenant: TenantOut
    # The first admin's one-time link, shown once (never stored).
    invitation_url: str | None = None
    invitation_expires_at: datetime | None = None


class InvitationCreate(BaseModel):
    email: str


class SupportGrantForOperator(BaseModel):
    """What an operator sees of a grant: enough to use it, nothing more."""

    id: str
    access: Literal["read", "write"]
    reason: str
    granted_by: str
    expires_at: datetime


class SupportEntry(BaseModel):
    url: str
    expires_in_seconds: int


class PlatformAuditEvent(BaseModel):
    id: str
    occurred_at: datetime
    operator_name: str
    action: str
    outcome: str
    tenant_id: str | None
    detail: dict
