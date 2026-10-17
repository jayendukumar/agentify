"""whole-process execution: a case walks all of a process's deployed agents

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3

Epic 39, US39.17. A process execution is one case through the process's
real flow; each agent node runs as an ordinary execution of that node's
live deployment, under it (`agent_executions.parent_execution_id`, with
the gateway question the agent answers in `routing`). A node with no live
deployment is a human task in the review inbox, so a review task may
belong to a process execution instead of an agent execution. A process
can be paused or stopped as a whole (`process_runtime`).
"""

import sqlalchemy as sa
from alembic import op

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None

TABLES = ("process_executions", "process_runtime")


def _tenant_id() -> sa.Column:
    return sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                     server_default=sa.text("app_current_tenant()"))


def upgrade() -> None:
    op.create_table(
        "process_executions",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=True),
        sa.Column("trigger", sa.String(), nullable=False, server_default="api"),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("final_output", sa.JSON(), nullable=True),
        sa.Column("deviations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("node_runs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("visited_path", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("resume_state", sa.JSON(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("process_id", "idempotency_key", name="uq_process_executions_idempotency"),
    )
    op.create_index("ix_process_executions_process_id", "process_executions", ["process_id"])
    op.create_index("ix_process_executions_status", "process_executions", ["status"])
    op.create_table(
        "process_runtime",
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True),
        _tenant_id(),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("agent_executions", sa.Column("parent_execution_id", sa.String(), nullable=True))
    op.create_index("ix_agent_executions_parent_execution_id", "agent_executions", ["parent_execution_id"])
    op.add_column("agent_executions", sa.Column("routing", sa.JSON(), nullable=True))
    op.alter_column("review_tasks", "execution_id", existing_type=sa.String(), nullable=True)
    op.alter_column("review_tasks", "deployment_id", existing_type=sa.String(), nullable=True)
    op.add_column("review_tasks", sa.Column("process_execution_id", sa.String(), nullable=True))
    op.add_column("review_tasks", sa.Column("node_id", sa.String(), nullable=True))
    op.create_index("ix_review_tasks_process_execution_id", "review_tasks", ["process_execution_id"])
    for table in TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} "
                   "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())")


def downgrade() -> None:
    op.execute("DELETE FROM review_tasks WHERE execution_id IS NULL")
    op.drop_index("ix_review_tasks_process_execution_id", table_name="review_tasks")
    op.drop_column("review_tasks", "node_id")
    op.drop_column("review_tasks", "process_execution_id")
    op.alter_column("review_tasks", "deployment_id", existing_type=sa.String(), nullable=False)
    op.alter_column("review_tasks", "execution_id", existing_type=sa.String(), nullable=False)
    op.drop_column("agent_executions", "routing")
    op.drop_index("ix_agent_executions_parent_execution_id", table_name="agent_executions")
    op.drop_column("agent_executions", "parent_execution_id")
    for table in reversed(TABLES):
        op.drop_table(table)
