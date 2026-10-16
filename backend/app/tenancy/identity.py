"""Epic 38, US38.6: each tenant brings its own identity provider.

`resolve()` answers "how do this tenant's people sign in?" for the
current request:

- **A `tenant_identity` row** (saved by the tenant's admins): its IdP, MFA
  rules and SCIM link claim. Redirect, sign-out and SCIM URLs are derived
  from the tenant's own host, so a sign-in started on `acme.example`
  comes back to `acme.example`.
- **No row, first tenant** (`TENANT_DEFAULT_SLUG`): the `OIDC_*`,
  `AUTH_MFA_*` and `SCIM_LINK_CLAIM` environment variables, exactly as
  before Epic 38, with their URLs as configured.
- **No row, any other tenant:** no single sign-on. Local sign-in outside
  production; in production nobody can sign in until it's configured.

The client secret is never stored: a tenant holds a *reference*,
`secret://tenant/<name>`, resolved in its own secrets store when it's
needed (US38.10, app/secret_store). A reference can't reach a platform secret
(the model provider's key) or another tenant's, so an admin can't have
one sent to an IdP they control. The first tenant may still name an
environment variable (`env:OIDC_CLIENT_SECRET`), as before Epic 38.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import secret_store
from app.config import Settings, get_settings
from app.db.models import TenantIdentityModel, TenantModel
from app.db.session import get_db

from .context import current_host, current_tenant_id

AuthMode = Literal["local", "oidc", "none"]
DEFAULT_AMR = ("mfa", "otp", "hwk", "swk")


@dataclass(frozen=True)
class IdentityConfig:
    auth_mode: AuthMode
    # tenant (a saved row) | environment (the first tenant's defaults) | unset
    source: str
    tenant_id: str | None = None
    issuer: str = ""
    discovery_url: str = ""
    client_id: str = ""
    client_secret_ref: str = ""
    display_name: str = "single sign-on"
    scopes: str = "openid profile email"
    acr_values: str = ""
    groups_claim: str = "groups"
    jit_provisioning: bool = True
    require_mfa: bool = True
    mfa_amr_values: tuple[str, ...] = DEFAULT_AMR
    mfa_acr_values: tuple[str, ...] = ()
    scim_link_claim: str = "sub"
    # OIDC_GROUP_ROLES, seeded into the mappings once -- the first tenant only.
    seed_group_roles: dict[str, list[str]] = field(default_factory=dict, hash=False, compare=False)
    redirect_uri: str = ""
    frontend_url: str = ""
    api_url: str = ""
    # Whose secrets `client_secret_ref` reaches: the tenant's slug, or
    # secret_store.PLATFORM for the operator console. None reaches nothing.
    secret_scope: str | None = None

    @property
    def client_secret(self) -> str:
        return secret_store.resolve(self.client_secret_ref, self.secret_scope) or ""

    @property
    def scim_base_url(self) -> str:
        return self.api_url.rstrip("/") + "/scim/v2"

    @property
    def backchannel_logout_url(self) -> str:
        return self.api_url.rstrip("/") + "/api/auth/oidc/backchannel-logout"

    def provider_key(self) -> tuple:
        """What an OIDC provider (and its caches) depends on."""
        return (self.tenant_id, self.issuer, self.discovery_url, self.client_id, self.client_secret_ref,
                self.redirect_uri, self.scopes, self.acr_values, self.mfa_amr_values, self.mfa_acr_values)


# ---- secret references --------------------------------------------------------------


class IdentityError(ValueError):
    """Settings an admin can't save, with the reason to show them."""


DEFAULT_SECRET_REF = "secret://tenant/oidc_client_secret"


def check_secret_ref(ref: str, slug: str, settings: Settings) -> None:
    try:
        secret_store.check_ref(ref, slug, example=DEFAULT_SECRET_REF, first_tenant_slug=settings.tenant_default_slug)
    except secret_store.SecretRefError as exc:
        raise IdentityError(f"The client secret: {exc}") from exc


# ---- URLs ----------------------------------------------------------------------------


def with_host(url: str, host: str) -> str:
    """`http://localhost:3000` on host `acme.localhost` -> `http://acme.localhost:3000`:
    the configured scheme and port, the tenant's host."""
    parts = urlsplit(url)
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def check_idp_url(url: str, label: str, settings: Settings) -> None:
    """In production a tenant's IdP must be a public https address: the API
    fetches it, and sends the client secret to its token endpoint, so an
    internal address would let an admin probe the platform's network."""
    if not url:
        return
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise IdentityError(f"{label} must be an http(s) URL")
    if settings.environment != "production":
        return
    if parts.scheme != "https":
        raise IdentityError(f"{label} must use https")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except OSError as exc:
        raise IdentityError(f"{label}: {parts.hostname} doesn't resolve") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise IdentityError(f"{label} points to a private address ({ip})")


# ---- resolving ---------------------------------------------------------------------


_slugs: dict[str, str] = {}


def _slug(db: Session, tenant_id: str) -> str | None:
    if tenant_id not in _slugs:
        slug = db.scalar(select(TenantModel.slug).where(TenantModel.id == tenant_id))
        if slug is None:
            return None
        _slugs[tenant_id] = slug  # slugs never change
    return _slugs[tenant_id]


def from_settings(settings: Settings, tenant_id: str | None = None) -> IdentityConfig:
    """The first tenant's defaults: the environment, as before Epic 38."""
    mode: AuthMode = "oidc" if settings.auth_mode == "oidc" else "local"
    return IdentityConfig(
        auth_mode=mode, source="environment", tenant_id=tenant_id,
        issuer=settings.oidc_issuer, discovery_url=settings.oidc_discovery_url, client_id=settings.oidc_client_id,
        client_secret_ref=f"env:{settings.oidc_client_secret_env}", display_name=settings.oidc_display_name,
        scopes=settings.oidc_scopes, acr_values=settings.oidc_acr_values, groups_claim=settings.oidc_groups_claim,
        jit_provisioning=settings.oidc_jit_provisioning, require_mfa=settings.auth_require_mfa,
        mfa_amr_values=tuple(settings.auth_mfa_amr_values), mfa_acr_values=tuple(settings.auth_mfa_acr_values),
        scim_link_claim=settings.scim_link_claim, seed_group_roles=dict(settings.oidc_group_roles),
        redirect_uri=settings.oidc_redirect_uri, frontend_url=settings.frontend_url.rstrip("/"),
        api_url=settings.api_public_url.rstrip("/"), secret_scope=settings.tenant_default_slug,
    )


def from_row(row: TenantIdentityModel, settings: Settings, host: str, slug: str | None) -> IdentityConfig:
    api_url = with_host(settings.api_public_url, host)
    return IdentityConfig(
        auth_mode="oidc" if row.auth_mode == "oidc" else "local", source="tenant", tenant_id=row.tenant_id,
        issuer=row.issuer, discovery_url=row.discovery_url, client_id=row.client_id,
        client_secret_ref=row.client_secret_ref, display_name=row.display_name, scopes=row.scopes,
        acr_values=row.acr_values, groups_claim=row.groups_claim, jit_provisioning=row.jit_provisioning,
        require_mfa=row.require_mfa, mfa_amr_values=tuple(row.mfa_amr_values or ()),
        mfa_acr_values=tuple(row.mfa_acr_values or ()), scim_link_claim=row.scim_link_claim,
        redirect_uri=api_url + "/api/auth/oidc/callback", frontend_url=with_host(settings.frontend_url, host),
        api_url=api_url, secret_scope=slug,
    )


def resolve(db: Session, *, tenant_id: str | None = None, host: str | None = None) -> IdentityConfig:
    """This tenant's sign-in configuration. Defaults to the current tenant
    and the host the request came in on (app/tenancy/context.py)."""
    settings = get_settings()
    tenant_id = tenant_id or current_tenant_id()
    host = host or current_host() or (urlsplit(settings.api_public_url).hostname or "localhost")
    if tenant_id is None:
        return IdentityConfig(auth_mode="none", source="unset")
    cached = db.info.get("tenant_identity")
    if cached is not None and cached[0] == (tenant_id, host):
        return cached[1]
    config = _resolve(db, settings, tenant_id, host)
    db.info["tenant_identity"] = ((tenant_id, host), config)
    return config


def forget(db: Session) -> None:
    """After saving a tenant's settings, so this session reads them anew."""
    db.info.pop("tenant_identity", None)


def get_identity(db: Annotated[Session, Depends(get_db, scope="function")]) -> IdentityConfig:
    """FastAPI dependency: the request's tenant's sign-in configuration."""
    return resolve(db)


def _resolve(db: Session, settings: Settings, tenant_id: str, host: str) -> IdentityConfig:
    row = db.scalar(select(TenantIdentityModel))  # row-level security: this tenant's, if any
    if row is not None:
        return from_row(row, settings, host, _slug(db, tenant_id))
    if _slug(db, tenant_id) == settings.tenant_default_slug:
        return from_settings(settings, tenant_id)
    return IdentityConfig(
        auth_mode="none" if settings.environment == "production" else "local", source="unset", tenant_id=tenant_id,
        redirect_uri=with_host(settings.api_public_url, host) + "/api/auth/oidc/callback",
        frontend_url=with_host(settings.frontend_url, host), api_url=with_host(settings.api_public_url, host),
    )


def tenant_slug(db: Session, tenant_id: str) -> str | None:
    return _slug(db, tenant_id)


def current_slug() -> str | None:
    """The current tenant's slug: whose secrets a reference reaches."""
    tenant_id = current_tenant_id()
    return _slug_cached(tenant_id) if tenant_id else None


def _slug_cached(tenant_id: str) -> str | None:
    if tenant_id not in _slugs:
        from app.db.session import get_session_factory

        with get_session_factory()() as db:
            return _slug(db, tenant_id)
    return _slugs[tenant_id]


# ---- what an admin may save ---------------------------------------------------------


EDITABLE = (
    "auth_mode", "issuer", "discovery_url", "client_id", "client_secret_ref", "display_name", "scopes",
    "acr_values", "groups_claim", "jit_provisioning", "require_mfa", "mfa_amr_values", "mfa_acr_values",
    "scim_link_claim",
)


def validate(values: dict, slug: str, settings: Settings) -> None:
    """Raises IdentityError with the reason to show the admin."""
    production = settings.environment == "production"
    if values["auth_mode"] == "local":
        if production:
            raise IdentityError("Local sign-in isn't available in production: anyone could sign in as anyone")
        return
    missing = [label for key, label in (("issuer", "issuer"), ("client_id", "client id"),
                                        ("client_secret_ref", "client secret reference")) if not values[key].strip()]
    if missing:
        raise IdentityError(f"Single sign-on needs the {', '.join(missing)}")
    check_secret_ref(values["client_secret_ref"], slug, settings)
    if "openid" not in values["scopes"].split():
        raise IdentityError("The scopes must include openid")
    if not values["scim_link_claim"].strip() or not values["groups_claim"].strip():
        raise IdentityError("The groups claim and the SCIM link claim can't be empty")
    if not values["require_mfa"] and production:
        raise IdentityError("Multi-factor authentication can't be turned off in production")
    if values["require_mfa"] and not (values["mfa_amr_values"] or values["mfa_acr_values"]):
        raise IdentityError("Say what counts as MFA: at least one amr or acr value")
    # Last: in production these resolve the IdP's host name.
    check_idp_url(values["issuer"], "The issuer", settings)
    check_idp_url(values["discovery_url"], "The discovery URL", settings)


def candidate(values: dict, tenant_id: str, settings: Settings, host: str) -> IdentityConfig:
    """The configuration these values would give, before saving them."""
    return from_row(TenantIdentityModel(tenant_id=tenant_id, **values), settings, host, _slug_cached(tenant_id))
