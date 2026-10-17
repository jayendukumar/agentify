"""signed event subscriptions that start an agent

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2

Epic 39, US39.16: a deployment can be started by a webhook from the
tenant's own systems. A subscription holds a reference to the signing
secret (never the value), where the signature, timestamp and event id are,
and how the event maps onto the agent's input.
"""

import sqlalchemy as sa
from alembic import op

revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_subscriptions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("app_current_tenant()")),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("secret_ref", sa.String(), nullable=False),
        sa.Column("signature_header", sa.String(), nullable=False),
        sa.Column("signature_prefix", sa.String(), nullable=False, server_default=""),
        sa.Column("timestamp_header", sa.String(), nullable=False),
        sa.Column("event_id_path", sa.String(), nullable=False),
        sa.Column("input_mapping", sa.JSON(), nullable=False),
        sa.Column("event_filter", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_event_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_event_subscriptions_deployment_id", "event_subscriptions", ["deployment_id"])
    op.create_index("ix_event_subscriptions_tenant_id", "event_subscriptions", ["tenant_id"])
    op.execute("ALTER TABLE event_subscriptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE event_subscriptions FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON event_subscriptions "
               "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade() -> None:
    op.drop_table("event_subscriptions")
