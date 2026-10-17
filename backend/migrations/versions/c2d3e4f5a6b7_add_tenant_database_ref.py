"""add a tenant's dedicated database

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6

Epic 38, US38.14: `tenants.database_ref`, a secret reference to the app
role's URL of the tenant's own database. NULL (every tenant so far) keeps
it in the shared database.
"""

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("database_ref", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "database_ref")
