"""add agent artifact release-candidate/approval columns

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 23, US23.1/US23.4: the only two lifecycle states that are
    # explicit human actions -- everything else is computed on read.
    op.add_column("agent_artifacts", sa.Column("marked_release_candidate_at", sa.DateTime(), nullable=True))
    op.add_column("agent_artifacts", sa.Column("approved_at", sa.DateTime(), nullable=True))
    op.add_column("agent_artifacts", sa.Column("approved_by", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_agent_artifacts_approved_by", "agent_artifacts", "users", ["approved_by"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_artifacts_approved_by", "agent_artifacts", type_="foreignkey")
    op.drop_column("agent_artifacts", "approved_by")
    op.drop_column("agent_artifacts", "approved_at")
    op.drop_column("agent_artifacts", "marked_release_candidate_at")
