"""add the agent runtime

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7

Epic 39, build step 2: agent identities, deployments, adapter bindings,
runtime clients, executions and their append-only steps. Every table is a
tenant table with row-level security, like the rest (Epic 38).
"""

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None

TABLES = ("agent_identities", "agent_deployments", "adapter_bindings", "runtime_clients", "agent_executions",
          "execution_steps")


def _tenant_id() -> sa.Column:
    return sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                     server_default=sa.text("app_current_tenant()"))


def _user_fk(name: str) -> sa.Column:
    return sa.Column(name, sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def upgrade() -> None:
    op.create_table(
        "agent_identities",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("directory_client_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        _user_fk("revoked_by"),
    )
    op.create_table(
        "agent_deployments",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_artifact_id", sa.String(), sa.ForeignKey("agent_artifacts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("group_key", sa.String(), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("environment", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("identity_id", sa.String(), sa.ForeignKey("agent_identities.id"), nullable=False),
        sa.Column("shadow_waiver", sa.Text(), nullable=True),
        _user_fk("created_by"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        _user_fk("approved_by"),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.Column("stopped_by", sa.String(), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
    )
    for column in ("process_id", "agent_artifact_id", "group_key", "status"):
        op.create_index(f"ix_agent_deployments_{column}", "agent_deployments", [column])
    op.create_table(
        "adapter_bindings",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("system_name", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=True),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("operations", sa.JSON(), nullable=False),
        sa.Column("credential", sa.JSON(), nullable=False),
        sa.Column("accepted_differences", sa.JSON(), nullable=False),
        _user_fk("updated_by"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("deployment_id", "system_name", name="uq_adapter_bindings_system"),
    )
    op.create_index("ix_adapter_bindings_deployment_id", "adapter_bindings", ["deployment_id"])
    op.create_table(
        "runtime_clients",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        _user_fk("created_by"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("deployment_id", sa.String(), sa.ForeignKey("agent_deployments.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("identity_id", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("final_output", sa.JSON(), nullable=True),
        sa.Column("deviations", sa.JSON(), nullable=False),
        sa.Column("resume_state", sa.JSON(), nullable=True),
        sa.Column("callback_url", sa.String(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("turns_used", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("deployment_id", "idempotency_key", name="uq_agent_executions_idempotency"),
    )
    for column in ("deployment_id", "process_id", "status", "created_at"):
        op.create_index(f"ix_agent_executions_{column}", "agent_executions", [column])
    op.create_table(
        "execution_steps",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_id(),
        sa.Column("execution_id", sa.String(), sa.ForeignKey("agent_executions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("step", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("execution_id", "attempt", "seq", name="uq_execution_steps_seq"),
    )
    op.create_index("ix_execution_steps_execution_id", "execution_steps", ["execution_id"])

    for table in TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        # The app role's privileges come from the default privileges set in
        # f2a3b4c5d6e7; row-level security keeps tenants apart.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
