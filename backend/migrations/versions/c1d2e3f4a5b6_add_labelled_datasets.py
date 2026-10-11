"""add labelled datasets and dataset rows; link scenarios and runs to rows

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
"""

from alembic import op
import sqlalchemy as sa


revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 32.
    op.create_table(
        "labelled_datasets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "agent_artifact_id", sa.String(), sa.ForeignKey("agent_artifacts.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_labelled_datasets_process_id", "labelled_datasets", ["process_id"])
    op.create_index("ix_labelled_datasets_agent_artifact_id", "labelled_datasets", ["agent_artifact_id"])
    op.create_table(
        "dataset_rows",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_id", sa.String(), sa.ForeignKey("labelled_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("row_key", sa.String(), nullable=False),
        sa.Column("valid_from_version", sa.Integer(), nullable=False),
        sa.Column("valid_to_version", sa.Integer(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("segment", sa.String(), nullable=False, server_default="typical"),
        sa.Column("provenance", sa.String(), nullable=False, server_default="expert"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("ambiguous", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ambiguity_note", sa.Text(), nullable=True),
        sa.Column("second_labels", sa.JSON(), nullable=True),
        sa.Column("holdout", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("critical", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("confirmed_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_dataset_rows_dataset_id", "dataset_rows", ["dataset_id"])
    op.create_index("ix_dataset_rows_dataset_key", "dataset_rows", ["dataset_id", "row_key"])
    op.add_column("twin_scenarios", sa.Column("dataset_id", sa.String(), nullable=True))
    op.add_column("twin_scenarios", sa.Column("decision_field", sa.String(), nullable=False, server_default="decision"))
    op.add_column("simulation_runs", sa.Column("dataset_id", sa.String(), nullable=True))
    op.add_column("simulation_runs", sa.Column("dataset_version", sa.Integer(), nullable=True))
    op.add_column("simulation_runs", sa.Column("row_key", sa.String(), nullable=True))
    op.add_column("simulation_runs", sa.Column("trial", sa.Integer(), nullable=True))
    op.add_column("simulation_runs", sa.Column("batch_id", sa.String(), nullable=True))
    op.create_index("ix_simulation_runs_dataset_id", "simulation_runs", ["dataset_id"])
    op.create_index("ix_simulation_runs_batch_id", "simulation_runs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_simulation_runs_batch_id", "simulation_runs")
    op.drop_index("ix_simulation_runs_dataset_id", "simulation_runs")
    for column in ("batch_id", "trial", "row_key", "dataset_version", "dataset_id"):
        op.drop_column("simulation_runs", column)
    op.drop_column("twin_scenarios", "decision_field")
    op.drop_column("twin_scenarios", "dataset_id")
    op.drop_table("dataset_rows")
    op.drop_table("labelled_datasets")
