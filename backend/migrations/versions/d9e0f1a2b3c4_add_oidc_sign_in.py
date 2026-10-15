"""add OIDC sign-in: user identities, login requests, IdP details on sessions

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("issuer", sa.String(), primary_key=True),
        sa.Column("subject", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_sign_in_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])
    op.create_table(
        "oidc_login_requests",
        sa.Column("state_hash", sa.String(), primary_key=True),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("return_to", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.add_column("sessions", sa.Column("auth_method", sa.String(), nullable=False, server_default="local"))
    op.add_column("sessions", sa.Column("idp_issuer", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("idp_subject", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("idp_sid", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("idp_id_token", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("idp_refresh_token", sa.Text(), nullable=True))
    op.create_index("ix_sessions_idp_sid", "sessions", ["idp_sid"])


def downgrade() -> None:
    op.drop_index("ix_sessions_idp_sid", table_name="sessions")
    for column in ("idp_refresh_token", "idp_id_token", "idp_sid", "idp_subject", "idp_issuer", "auth_method"):
        op.drop_column("sessions", column)
    op.drop_table("oidc_login_requests")
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
