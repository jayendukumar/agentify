"""add review tasks, limits and automatic pause to the agent runtime

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8

Epic 39, build step 3: the review inbox (review_tasks), in-app
notifications, per-tool circuit breakers, and on deployments the caps
across executions, the pause rules, the review SLA and what new work does
while paused.
"""

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None

TABLES = ("review_tasks", "runtime_notifications", "tool_circuits")


def _tenant_id() -> sa.Column:
    return sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                     server_default=sa.text("app_current_tenant()"))


def upgrade() -> None:
    op.add_column("agent_deployments", sa.Column("limits", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("agent_deployments", sa.Column("pause_rules", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("agent_deployments", sa.Column("review_sla_minutes", sa.Integer(), nullable=False, server_default="240"))
    op.add_column("agent_deployments", sa.Column("when_paused", sa.String(), nullable=False, server_default="refuse"))
    op.add_column("agent_deployments", sa.Column("paused_at", sa.DateTime(), nullable=True))
    op.add_column("agent_deployments", sa.Column("pause_reason", sa.Text(), nullable=True))
    op.add_column("agent_executions", sa.Column("requested_by", sa.String(), nullable=True))
    op.add_column("agent_executions", sa.Column("resolution", sa.JSON(), nullable=True))

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("execution_id", sa.String(), sa.ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("proposed_action", sa.JSON(), nullable=False),
        sa.Column("case_input", sa.JSON(), nullable=False),
        sa.Column("assignee_role", sa.String(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    for column in ("execution_id", "deployment_id", "process_id", "status", "due_at"):
        op.create_index(f"ix_review_tasks_{column}", "review_tasks", [column])
    op.create_table(
        "runtime_notifications",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=True),
        sa.Column("deployment_id", sa.String(), nullable=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_runtime_notifications_user_id", "runtime_notifications", ["user_id"])
    op.create_index("ix_runtime_notifications_created_at", "runtime_notifications", ["created_at"])
    op.create_table(
        "tool_circuits",
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("system_name", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("recent", sa.JSON(), nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )

    for table in TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
    for column in ("resolution", "requested_by"):
        op.drop_column("agent_executions", column)
    for column in ("pause_reason", "paused_at", "when_paused", "review_sla_minutes", "pause_rules", "limits"):
        op.drop_column("agent_deployments", column)
