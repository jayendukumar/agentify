"""add process twin: sample-data systems, scenario routing, operating models, process twin batches

Revision ID: a6b7c8d9e0f1
Revises: f4a5b6c7d8e9
"""

from alembic import op
import sqlalchemy as sa


revision = "a6b7c8d9e0f1"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 36, US36.1 / US36.2-36.3.
    op.add_column("twin_environments", sa.Column("system_data", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("orchestration_scenarios", sa.Column("routing", sa.JSON(), nullable=False, server_default="{}"))
    # US36.4.
    op.create_table(
        "process_operating_models",
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    # US36.5-36.7.
    op.create_table(
        "process_twin_batches",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("row_keys", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("operating_model", sa.JSON(), nullable=False),
        sa.Column("operating_model_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_process_twin_batches_process_id", "process_twin_batches", ["process_id"])


def downgrade() -> None:
    op.drop_index("ix_process_twin_batches_process_id", table_name="process_twin_batches")
    op.drop_table("process_twin_batches")
    op.drop_table("process_operating_models")
    op.drop_column("orchestration_scenarios", "routing")
    op.drop_column("twin_environments", "system_data")
