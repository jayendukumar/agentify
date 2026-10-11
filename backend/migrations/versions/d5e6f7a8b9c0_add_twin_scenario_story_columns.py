"""add guided-builder columns to twin scenarios

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
"""

from alembic import op
import sqlalchemy as sa


revision = "d5e6f7a8b9c0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 24, US24.4/US24.6: all additive with server defaults, so every
    # pre-existing scenario keeps grading exactly as before ("exact").
    op.add_column("twin_scenarios", sa.Column("faults", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("twin_scenarios", sa.Column("step_match", sa.String(), nullable=False, server_default="exact"))
    op.add_column("twin_scenarios", sa.Column("story", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("twin_scenarios", "story")
    op.drop_column("twin_scenarios", "step_match")
    op.drop_column("twin_scenarios", "faults")
