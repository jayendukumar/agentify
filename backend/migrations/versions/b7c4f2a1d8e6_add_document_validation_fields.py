"""add document process-definition validation fields

Revision ID: b7c4f2a1d8e6
Revises: 9b055947e7f9
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c4f2a1d8e6"
down_revision = "9b055947e7f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("process_definition_confidence", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("validation_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "validation_message")
    op.drop_column("documents", "process_definition_confidence")
