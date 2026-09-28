"""add orchestration rehearsal tables

Revision ID: d4f7a2b91c3e
Revises: b7c4f2a1d8e6
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f7a2b91c3e"
down_revision = "b7c4f2a1d8e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orchestration_scenarios",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=False),
        sa.Column("baseline_version_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("gateway_decisions", sa.JSON(), nullable=False),
        sa.Column("manual_node_config", sa.JSON(), nullable=False),
        sa.Column("system_stubs", sa.JSON(), nullable=False),
        sa.Column("human_checkpoint_config", sa.JSON(), nullable=False),
        sa.Column("data_mapping_mode", sa.JSON(), nullable=False),
        sa.Column("expected_path", sa.JSON(), nullable=False),
        sa.Column("expected_final_output", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_orchestration_scenarios_process_id"), "orchestration_scenarios", ["process_id"], unique=False
    )

    op.create_table(
        "orchestration_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("node_runs", sa.JSON(), nullable=False),
        sa.Column("handoffs", sa.JSON(), nullable=False),
        sa.Column("visited_path", sa.JSON(), nullable=False),
        sa.Column("final_output", sa.JSON(), nullable=True),
        sa.Column("deviations", sa.JSON(), nullable=False),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("run_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["scenario_id"], ["orchestration_scenarios.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orchestration_runs_scenario_id"), "orchestration_runs", ["scenario_id"], unique=False)
    op.create_index(op.f("ix_orchestration_runs_process_id"), "orchestration_runs", ["process_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_orchestration_runs_process_id"), table_name="orchestration_runs")
    op.drop_index(op.f("ix_orchestration_runs_scenario_id"), table_name="orchestration_runs")
    op.drop_table("orchestration_runs")
    op.drop_index(op.f("ix_orchestration_scenarios_process_id"), table_name="orchestration_scenarios")
    op.drop_table("orchestration_scenarios")
