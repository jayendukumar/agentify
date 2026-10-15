"""Epic 38: many organizations ("tenants") on one deployment, kept apart
by Postgres row-level security. See planning/epics/38-multi-tenancy.md."""

from .context import Tenant, current_tenant_id, tenant_scope
from .hosts import normalize_host, resolve_host

__all__ = ["Tenant", "current_tenant_id", "normalize_host", "resolve_host", "tenant_scope"]
