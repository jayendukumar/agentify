"""add the tenant lifecycle: suspension time, deletion wait, data exports

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5

Epic 38, US38.13.

- `tenants.suspended_at`, `tenants.delete_after`: a tenant's data can be
  erased only once it's been suspended, deleted, and the wait is over.
- `tenant_exports`: an admin's archive of the tenant's data, a tenant row.
- The console's role may now delete `tenants` and `tenant_domains` rows
  (once every tenant row is gone, which the app role does as the tenant),
  and update the `detail` of platform audit events, to strip a deleted
  tenant's personal data (a first admin's email) from them. It still has
  no privilege on any tenant table.
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def _platform_role() -> str:
    return os.environ.get("DATABASE_PLATFORM_ROLE", "axyntro_platform")


def upgrade() -> None:
    op.add_column("tenants", sa.Column("suspended_at", sa.DateTime(), nullable=True))
    op.add_column("tenants", sa.Column("delete_after", sa.DateTime(), nullable=True))
    op.execute("UPDATE tenants SET suspended_at = now() WHERE status IN ('suspended', 'deleting')")
    op.create_table(
        "tenant_exports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("app_current_tenant()")),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("requested_by_name", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(), nullable=True),
        sa.Column("downloaded_by_name", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column("table_counts", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_tenant_exports_tenant_id", "tenant_exports", ["tenant_id"])
    # Privileges from f2a3b4c5d6e7's defaults; row-level security keeps tenants apart.
    op.execute("ALTER TABLE tenant_exports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_exports FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_exports "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )
    bind = op.get_bind()
    role = _platform_role()
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
        op.execute(f'GRANT DELETE ON tenants, tenant_domains TO "{role}"')
        op.execute(f'GRANT UPDATE (detail) ON platform_audit_events TO "{role}"')


def downgrade() -> None:
    bind = op.get_bind()
    role = _platform_role()
    if bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
        op.execute(f'REVOKE DELETE ON tenants, tenant_domains FROM "{role}"')
        op.execute(f'REVOKE UPDATE (detail) ON platform_audit_events FROM "{role}"')
    op.drop_table("tenant_exports")
    op.drop_column("tenants", "delete_after")
    op.drop_column("tenants", "suspended_at")
