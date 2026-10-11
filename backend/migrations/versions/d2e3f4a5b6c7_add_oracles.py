"""add decision rules, metamorphic relations, judge rubrics

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from alembic import op
import sqlalchemy as sa


revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 33.
    op.create_table(
        "decision_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gateway_id", sa.String(), nullable=False),
        sa.Column("gateway_label", sa.String(), nullable=False, server_default=""),
        sa.Column("branch_target_id", sa.String(), nullable=False),
        sa.Column("branch_label", sa.String(), nullable=False, server_default=""),
        sa.Column("condition_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("expression", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(), nullable=False, server_default="proposed"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("control_id", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("confirmed_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_decision_rules_process_id", "decision_rules", ["process_id"])
    op.create_table(
        "metamorphic_relations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_id", sa.String(), sa.ForeignKey("labelled_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("perturbation", sa.JSON(), nullable=False),
        sa.Column("direction", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_metamorphic_relations_dataset_id", "metamorphic_relations", ["dataset_id"])
    op.create_table(
        "judge_rubrics",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("rubric", sa.Text(), nullable=False),
        sa.Column("judge_model", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("min_agreement", sa.Float(), nullable=False, server_default="0.85"),
        sa.Column("min_samples", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("calibration", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_judge_rubrics_process_id", "judge_rubrics", ["process_id"])


def downgrade() -> None:
    op.drop_table("judge_rubrics")
    op.drop_table("metamorphic_relations")
    op.drop_table("decision_rules")
