"""add simulation_runs table

Revision ID: e6f1a2b3c4d5
Revises: d4f7a2b91c3e
"""

from alembic import op
import sqlalchemy as sa


revision = "e6f1a2b3c4d5"
down_revision = "d4f7a2b91c3e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 17: the unified run store both twin and orchestration runs write
    # to going forward -- additive, not a migration of existing rows:
    # twin_runs/orchestration_runs are left in place unaltered (see
    # app/db/models.py's SimulationRunModel docstring and
    # planning/decision-log.md).
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("agent_artifact_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("node_runs", sa.JSON(), nullable=False),
        sa.Column("visited_path", sa.JSON(), nullable=False),
        sa.Column("final_output", sa.JSON(), nullable=True),
        sa.Column("deviations", sa.JSON(), nullable=False),
        sa.Column("graded_passed", sa.Boolean(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("turns_used", sa.Integer(), nullable=False),
        sa.Column("resume_state", sa.JSON(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("queued_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("run_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_artifact_id"], ["agent_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_simulation_runs_kind"), "simulation_runs", ["kind"], unique=False)
    op.create_index(op.f("ix_simulation_runs_process_id"), "simulation_runs", ["process_id"], unique=False)
    op.create_index(op.f("ix_simulation_runs_scenario_id"), "simulation_runs", ["scenario_id"], unique=False)
    op.create_index(
        op.f("ix_simulation_runs_agent_artifact_id"), "simulation_runs", ["agent_artifact_id"], unique=False
    )
    op.create_index(op.f("ix_simulation_runs_status"), "simulation_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_simulation_runs_status"), table_name="simulation_runs")
    op.drop_index(op.f("ix_simulation_runs_agent_artifact_id"), table_name="simulation_runs")
    op.drop_index(op.f("ix_simulation_runs_scenario_id"), table_name="simulation_runs")
    op.drop_index(op.f("ix_simulation_runs_process_id"), table_name="simulation_runs")
    op.drop_index(op.f("ix_simulation_runs_kind"), table_name="simulation_runs")
    op.drop_table("simulation_runs")
