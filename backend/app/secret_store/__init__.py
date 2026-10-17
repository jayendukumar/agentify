"""Epic 38, US38.10: each tenant's secrets, kept apart.

Stored configuration never holds a secret, only a reference to one:

- **`secret://tenant/<name>`**: the current tenant's secret called
  `<name>` (lowercase letters, digits, `_`, `-`). There's no way to name
  another tenant's secret, or a platform secret: the tenant comes from
  where the reference is used, never from the reference.
- **`env:<NAME>`**: an environment variable, as before Epic 38. Only the
  first tenant (`TENANT_DEFAULT_SLUG`) may use these, because on a
  single-organization deployment the operator *is* that organization.
  For any other tenant, an environment name could point at the model
  provider's key or another tenant's secret and send it to a server its
  admin controls.

The store is pluggable (`SECRETS_BACKEND`):

- `env` (default, local): `secret://tenant/oidc_client_secret` for tenant
  `northwind` is the environment variable
  `TENANT_NORTHWIND_OIDC_CLIENT_SECRET`.
- `files`: the same name as a file in `SECRETS_DIR`, for Docker or
  Kubernetes secrets mounted into the container.

A cloud secret manager is one more class with a `get(slug, name)`.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings

SECRET_SCHEME = "secret://tenant/"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SecretRefError(ValueError):
    """A reference that can't be saved here, with the reason to show."""


class SecretStore(Protocol):
    def get(self, tenant_slug: str, name: str) -> str | None: ...


def storage_key(tenant_slug: str, name: str) -> str:
    """`northwind`, `oidc_client_secret` -> `TENANT_NORTHWIND_OIDC_CLIENT_SECRET`."""
    return "TENANT_" + re.sub(r"[^A-Z0-9]", "_", f"{tenant_slug}_{name}".upper())


class EnvSecretStore:
    def get(self, tenant_slug: str, name: str) -> str | None:
        return os.environ.get(storage_key(tenant_slug, name)) or None


class FileSecretStore:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)

    def get(self, tenant_slug: str, name: str) -> str | None:
        path = self.directory / storage_key(tenant_slug, name)
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except FileNotFoundError:
            return None


@lru_cache
def get_store() -> SecretStore:
    settings = get_settings()
    if settings.secrets_backend == "files":
        return FileSecretStore(settings.secrets_dir)
    return EnvSecretStore()


def is_ref(value: str) -> bool:
    return value.startswith(SECRET_SCHEME)


def name_of(ref: str) -> str:
    return ref.removeprefix(SECRET_SCHEME)


def is_first_tenant(tenant_slug: str) -> bool:
    return tenant_slug == get_settings().tenant_default_slug


def check_ref(ref: str, tenant_slug: str, *, example: str = "secret://tenant/client_secret",
              first_tenant_slug: str | None = None) -> None:
    """Whether `ref` may be saved for this tenant."""
    first = first_tenant_slug or get_settings().tenant_default_slug
    if is_ref(ref):
        if not _NAME.match(name_of(ref)):
            raise SecretRefError(f"A secret name is lowercase letters, digits, '_' and '-', like {example}")
        return
    if ref.startswith("env:"):
        if tenant_slug != first:
            raise SecretRefError(f"Use a reference like {example}; environment variable names are only for the "
                                 "deployment's first organization")
        if not _ENV_NAME.match(ref.removeprefix("env:")):
            raise SecretRefError("An environment variable name is uppercase letters, digits and '_'")
        return
    raise SecretRefError(f"Give a reference to the secret, like {example}, not the secret itself")


# Whose secrets a reference reaches when it's the platform's own (the
# operator console's IdP), not a tenant's: environment names only.
PLATFORM = "<platform>"


def resolve(ref: str, tenant_slug: str | None) -> str | None:
    """The secret, or None if it isn't set. Checked again here, not only
    when saved, so a reference stored before these rules can't reach a
    secret it shouldn't. With no tenant, nothing resolves."""
    if tenant_slug is None:
        return None
    if is_ref(ref):
        if tenant_slug == PLATFORM or not _NAME.match(name_of(ref)):
            return None
        return get_store().get(tenant_slug, name_of(ref))
    if ref.startswith("env:"):
        if tenant_slug != PLATFORM and not is_first_tenant(tenant_slug):
            return None
        return os.environ.get(ref.removeprefix("env:")) or None
    return None


def _tenant_keys(keys: list[str], tenant_slug: str, other_slugs: list[str]) -> tuple[list[str], list[str]]:
    """The keys that are this tenant's, and those that might also be
    another's: `TENANT_ACME_EU_X` is tenant `acme`'s secret `eu_x`, or
    tenant `acme-eu`'s secret `x`. Those are left alone."""
    prefix = storage_key(tenant_slug, "")
    longer = [storage_key(s, "") for s in other_slugs if s != tenant_slug]
    longer = [p for p in longer if p.startswith(prefix)]
    mine = [k for k in keys if k.startswith(prefix) and len(k) > len(prefix)]
    return ([k for k in mine if not any(k.startswith(p) for p in longer)],
            [k for k in mine if any(k.startswith(p) for p in longer)])


def erase(tenant_slug: str, *, other_slugs: list[str]) -> dict:
    """Epic 38, US38.13: removes a deleted tenant's secrets where the store
    can, and lists those it can't (names only). Mounted files are deleted;
    environment variables belong to the deployment's configuration, so
    they're dropped from this process and listed for the operator to
    remove there."""
    settings = get_settings()
    if settings.secrets_backend == "files":
        directory = Path(settings.secrets_dir)
        names = [p.name for p in directory.iterdir() if p.is_file()] if directory.is_dir() else []
        mine, ambiguous = _tenant_keys(names, tenant_slug, other_slugs)
        removed = []
        for name in mine:
            try:
                (directory / name).unlink()
                removed.append(name)
            except OSError:
                pass
        return {"backend": "files", "removed": sorted(removed), "not_removed": sorted(set(mine) - set(removed)),
                "ambiguous_kept": sorted(ambiguous)}
    mine, ambiguous = _tenant_keys(list(os.environ), tenant_slug, other_slugs)
    for key in mine:
        os.environ.pop(key, None)
    return {"backend": "env", "removed": [], "remove_from_deployment_environment": sorted(mine),
            "ambiguous_kept": sorted(ambiguous)}


def where_to_put(ref: str, tenant_slug: str) -> str:
    """For an operator: where the store looks for this reference."""
    if not is_ref(ref):
        return ref.removeprefix("env:") + " (environment variable)"
    key = storage_key(tenant_slug, name_of(ref))
    settings = get_settings()
    if settings.secrets_backend == "files":
        return str(Path(settings.secrets_dir) / key)
    return key + " (environment variable)"
