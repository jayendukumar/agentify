"""add batch runs, batch schedules, run seeds

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from alembic import op
import sqlalchemy as sa


revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 34.
    op.add_column("simulation_runs", sa.Column("seed", sa.BigInteger(), nullable=True))
    op.create_table(
        "batch_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("agent_artifact_id", sa.String(), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="correctness"),
        sa.Column("tier", sa.String(), nullable=False, server_default="custom"),
        sa.Column("trials", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sample", sa.JSON(), nullable=False),
        sa.Column("row_keys", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("estimate", sa.JSON(), nullable=False),
        sa.Column("load_settings", sa.JSON(), nullable=False),
        sa.Column("keep_traces", sa.String(), nullable=False, server_default="failed_and_sampled"),
        sa.Column("schedule_id", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_batch_runs_process_id", "batch_runs", ["process_id"])
    op.create_index("ix_batch_runs_scenario_id", "batch_runs", ["scenario_id"])
    op.create_index("ix_batch_runs_agent_artifact_id", "batch_runs", ["agent_artifact_id"])
    op.create_table(
        "batch_schedules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False, server_default="full"),
        sa.Column("trials", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("every_minutes", sa.Integer(), nullable=True),
        sa.Column("on_dataset_change", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("on_context_change", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_dataset_version", sa.Integer(), nullable=True),
        sa.Column("last_context_pack_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_batch_schedules_process_id", "batch_schedules", ["process_id"])


def downgrade() -> None:
    op.drop_table("batch_schedules")
    op.drop_table("batch_runs")
    op.drop_column("simulation_runs", "seed")
