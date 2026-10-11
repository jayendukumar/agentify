"""add context packs and pack pins

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""

from alembic import op
import sqlalchemy as sa


revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 28.
    op.create_table(
        "context_packs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("content_hash", sa.String(), nullable=False, unique=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("process_id", sa.String(), sa.ForeignKey("processes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("passages", sa.JSON(), nullable=False),
        sa.Column("token_estimate", sa.Integer(), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.add_column("blueprint_overlays", sa.Column("context_pack_id", sa.String(), nullable=True))
    op.add_column("agent_artifacts", sa.Column("context_pack_id", sa.String(), nullable=True))
    op.add_column("gap_findings", sa.Column("citations", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("gap_findings", sa.Column("context_pack_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("gap_findings", "context_pack_id")
    op.drop_column("gap_findings", "citations")
    op.drop_column("agent_artifacts", "context_pack_id")
    op.drop_column("blueprint_overlays", "context_pack_id")
    op.drop_table("context_packs")
