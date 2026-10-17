"""let runtime clients sign in with the tenant's IdP (client credentials)

Revision ID: f6a7b8c9d0e1
Revises: f5a6b7c8d9e0

Epic 39, US39.4: a runtime client is either an OAuth client of the
tenant's IdP, whose access tokens are checked against its keys, or holds a
bearer token as before. One unrevoked client per OAuth client id.
"""

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("runtime_clients", "token_hash", existing_type=sa.String(), nullable=True)
    op.add_column("runtime_clients", sa.Column("oauth_client_id", sa.String(), nullable=True))
    op.add_column("runtime_clients", sa.Column("oauth_audience", sa.String(), nullable=True))
    op.create_index("uq_runtime_clients_oauth_client", "runtime_clients", ["tenant_id", "oauth_client_id"],
                    unique=True, postgresql_where=sa.text("oauth_client_id IS NOT NULL AND revoked_at IS NULL"))


def downgrade() -> None:
    op.execute("DELETE FROM runtime_clients WHERE token_hash IS NULL")
    op.drop_index("uq_runtime_clients_oauth_client", table_name="runtime_clients")
    op.drop_column("runtime_clients", "oauth_audience")
    op.drop_column("runtime_clients", "oauth_client_id")
    op.alter_column("runtime_clients", "token_hash", existing_type=sa.String(), nullable=False)
