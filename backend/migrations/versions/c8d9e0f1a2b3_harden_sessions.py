"""harden sessions: hashed, rotating access/refresh tokens, idle and absolute expiry, revocation

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 37, US37.12: every existing session's id *is* its cookie value,
    # stored in plain text. They can't be converted to hashed tokens
    # without that value doing the job, so everyone signs in again.
    op.execute("DELETE FROM sessions")
    op.alter_column("sessions", "created_at", server_default=None)
    op.add_column("sessions", sa.Column("last_used_at", sa.DateTime(), nullable=False))
    op.add_column("sessions", sa.Column("revoked_at", sa.DateTime(), nullable=True))
    op.add_column("sessions", sa.Column("revoked_reason", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("csrf_token", sa.String(), nullable=False))
    op.add_column("sessions", sa.Column("ip", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("user_agent", sa.String(), nullable=True))
    op.create_table(
        "session_tokens",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("session_id", sa.String(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_session_tokens_session_id", "session_tokens", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_session_tokens_session_id", table_name="session_tokens")
    op.drop_table("session_tokens")
    op.execute("DELETE FROM sessions")
    op.drop_column("sessions", "user_agent")
    op.drop_column("sessions", "ip")
    op.drop_column("sessions", "csrf_token")
    op.drop_column("sessions", "revoked_reason")
    op.drop_column("sessions", "revoked_at")
    op.drop_column("sessions", "last_used_at")
    op.alter_column("sessions", "created_at", server_default=sa.text("now()"))
