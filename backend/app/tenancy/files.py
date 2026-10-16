"""Epic 38, US38.10: each tenant's uploaded files under a directory of its
own, `<DOCUMENT_STORAGE_PATH>/tenants/<tenant_id>/`.

Paths are built here, from the current tenant, never from anything a
caller or an upload supplies: a file name is reduced to its last
component, and the result must stay inside the tenant's directory. With
no tenant set there's no directory, and storing or reading fails -- as
row-level security fails closed for rows.

Deleting a tenant (US38.13) removes its directory. Object storage (Critical
Review weakness 4) gives each tenant a prefix and its own encryption key.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

from .context import current_tenant_id

TENANTS_DIR = "tenants"


class TenantFileError(RuntimeError):
    pass


def tenant_dir(settings: Settings, tenant_id: str | None = None) -> Path:
    tenant_id = tenant_id or current_tenant_id()
    if not tenant_id or not re.fullmatch(r"[A-Za-z0-9_-]+", tenant_id):
        raise TenantFileError("Files can only be stored or read inside a tenant")
    return settings.document_storage_root / TENANTS_DIR / tenant_id


def safe_name(filename: str) -> str:
    """The upload's own name, without any directories (`..\\..\\x.pdf` ->
    `x.pdf`), so it can't climb out of the tenant's directory."""
    name = re.split(r"[\\/]", filename)[-1].strip().lstrip(".")
    return name or "unnamed"


def tenant_file(settings: Settings, *parts: str) -> Path:
    """`<tenant dir>/<parts...>`, refused if it would land outside it."""
    base = tenant_dir(settings).resolve()
    path = base.joinpath(*parts[:-1], safe_name(parts[-1])).resolve()
    if base not in path.parents:
        raise TenantFileError("That file path leaves the tenant's directory")
    return path
