"""add release criteria to agent artifacts

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

from alembic import op
import sqlalchemy as sa


revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 35, US35.7.
    op.add_column("agent_artifacts", sa.Column("release_criteria", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_artifacts", "release_criteria")
