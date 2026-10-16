"""add per-tenant limits and LLM usage records

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9

Epic 38, US38.9.

- Limits on `tenants` (a platform table, so the operator console's role
  sets them and the app role only reads them): concurrent jobs, LLM
  requests per minute, a monthly LLM budget. NULL means the deployment's
  default (`TENANT_*` settings), which is "no limit" unless set.
- `llm_usage_events`: one row per LLM call, as the tenant.
- `llm_usage_monthly`: the same, summed per month, operation and model,
  so the budget check before every call and the usage page read a few
  rows rather than a month of calls.
"""

import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

TABLES = ("llm_usage_events", "llm_usage_monthly")


def _tenant_column() -> sa.Column:
    return sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                     server_default=sa.text("app_current_tenant()"))


def upgrade() -> None:
    op.add_column("tenants", sa.Column("max_concurrent_jobs", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("llm_requests_per_minute", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("llm_monthly_budget_usd", sa.Float(), nullable=True))

    op.create_table(
        "llm_usage_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        _tenant_column(),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
    )
    op.create_index("ix_llm_usage_events_tenant_occurred", "llm_usage_events", ["tenant_id", "occurred_at"])
    op.create_table(
        "llm_usage_monthly",
        _tenant_column(),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("calls_without_cost", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "month", "operation", "model"),
    )
    # The app role's privileges come from the default privileges set in
    # f2a3b4c5d6e7; row-level security is what keeps tenants apart.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    for column in ("llm_monthly_budget_usd", "llm_requests_per_minute", "max_concurrent_jobs"):
        op.drop_column("tenants", column)
