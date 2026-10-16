"""add per-tenant registries and whose key paid for LLM usage

Revision ID: f9a0b1c2d3e4
Revises: d6e7f8a9b0c1

Epic 38, US38.11.

- `registries`: a tenant's agent registries, replacing the deployment-wide
  REGISTRIES environment variable (which stays as the first tenant's
  default while it has no rows).
- `key_source` on the usage tables: `platform` or `tenant`, so usage is
  attributed to the tenant either way and the monthly budget can count
  the platform's key only. Existing rows were all on the platform's key.
"""

import sqlalchemy as sa
from alembic import op

revision = "f9a0b1c2d3e4"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("app_current_tenant()")),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_registries_tenant_name"),
    )
    op.create_index("ix_registries_tenant_id", "registries", ["tenant_id"])
    # The app role's privileges come from the default privileges set in
    # f2a3b4c5d6e7; row-level security keeps tenants apart.
    op.execute("ALTER TABLE registries ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE registries FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON registries "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )

    for table in ("llm_usage_events", "llm_usage_monthly"):
        op.add_column(table, sa.Column("key_source", sa.String(), nullable=False, server_default="platform"))
    op.drop_constraint("llm_usage_monthly_pkey", "llm_usage_monthly", type_="primary")
    op.create_primary_key("llm_usage_monthly_pkey", "llm_usage_monthly",
                          ["tenant_id", "month", "operation", "model", "key_source"])


def downgrade() -> None:
    op.drop_constraint("llm_usage_monthly_pkey", "llm_usage_monthly", type_="primary")
    op.execute("DELETE FROM llm_usage_monthly WHERE key_source <> 'platform'")
    op.create_primary_key("llm_usage_monthly_pkey", "llm_usage_monthly", ["tenant_id", "month", "operation", "model"])
    for table in ("llm_usage_monthly", "llm_usage_events"):
        op.drop_column(table, "key_source")
    op.drop_table("registries")
