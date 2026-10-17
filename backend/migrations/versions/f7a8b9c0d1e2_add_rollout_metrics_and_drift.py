"""shadow, canary and rollback; telemetry and alerts; drift against the twin

Revision ID: f7a8b9c0d1e2
Revises: f6a7b8c9d0e1

Epic 39, step 4 (US39.13-39.15). A deployment can run in `shadow` (a
copy of live work, nothing that changes data sent) or as a canary taking
`traffic_share` percent of its agent's cases, and remembers the version it
would replace. A shadow execution points at the live case it copies, and a
caller can report what really happened to a case. Per deployment: where
its traces go, its alert rules, and the state of its alerts and drift
checks.
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_deployments", sa.Column("traffic_share", sa.Integer(), nullable=True))
    op.add_column("agent_deployments", sa.Column("shadow_started_at", sa.DateTime(), nullable=True))
    op.add_column("agent_deployments", sa.Column("previous_deployment_id", sa.String(), nullable=True))
    op.add_column("agent_deployments", sa.Column("telemetry", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("agent_deployments", sa.Column("alert_rules", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("agent_deployments", sa.Column("monitor_state", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("agent_executions", sa.Column("shadow_of", sa.String(), nullable=True))
    op.create_index("ix_agent_executions_shadow_of", "agent_executions", ["shadow_of"])
    op.add_column("agent_executions", sa.Column("reported_outcome", sa.JSON(), nullable=True))
    op.add_column("agent_executions", sa.Column("reported_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_executions", "reported_at")
    op.drop_column("agent_executions", "reported_outcome")
    op.drop_index("ix_agent_executions_shadow_of", table_name="agent_executions")
    op.drop_column("agent_executions", "shadow_of")
    for column in ("monitor_state", "alert_rules", "telemetry", "previous_deployment_id", "shadow_started_at",
                   "traffic_share"):
        op.drop_column("agent_deployments", column)
