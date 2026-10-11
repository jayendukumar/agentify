"""add process system links

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
"""

from alembic import op
import sqlalchemy as sa


revision = "a9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 29, US29.3.
    op.create_table(
        "process_system_links",
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("system_name", sa.String(), primary_key=True),
        sa.Column(
            "application_id", sa.String(), sa.ForeignKey("context_entities.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("auto", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("linked_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("linked_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("process_system_links")
