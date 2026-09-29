"""add evaluation column, agent artifact version, and regression suite tables

Revision ID: f1a2b3c4d5e6
Revises: e6f1a2b3c4d5
"""

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e6f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 19, US19.5: an incrementing version identifier, bumped on every
    # regenerate of an existing agent_artifacts row.
    op.add_column(
        "agent_artifacts", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    # Epic 19, US19.1: the twelve-dimension EvaluationResult, computed once
    # a run reaches a terminal state -- see app/twin/evaluation.py.
    op.add_column("simulation_runs", sa.Column("evaluation", sa.JSON(), nullable=True))

    op.create_table(
        "regression_suites",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=False),
        sa.Column("agent_artifact_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("scenario_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["process_id"], ["processes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_artifact_id"], ["agent_artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_regression_suites_process_id"), "regression_suites", ["process_id"], unique=False)
    op.create_index(
        op.f("ix_regression_suites_agent_artifact_id"), "regression_suites", ["agent_artifact_id"], unique=False
    )

    op.create_table(
        "regression_suite_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("suite_id", sa.String(), nullable=False),
        sa.Column("run_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("started_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["suite_id"], ["regression_suites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["started_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_regression_suite_runs_suite_id"), "regression_suite_runs", ["suite_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_regression_suite_runs_suite_id"), table_name="regression_suite_runs")
    op.drop_table("regression_suite_runs")
    op.drop_index(op.f("ix_regression_suites_agent_artifact_id"), table_name="regression_suites")
    op.drop_index(op.f("ix_regression_suites_process_id"), table_name="regression_suites")
    op.drop_table("regression_suites")
    op.drop_column("simulation_runs", "evaluation")
    op.drop_column("agent_artifacts", "version")
