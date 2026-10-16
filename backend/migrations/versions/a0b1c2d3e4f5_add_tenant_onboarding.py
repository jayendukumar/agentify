"""add tenant onboarding: how a tenant was created, and first-admin invitations

Revision ID: a0b1c2d3e4f5
Revises: f9a0b1c2d3e4

Epic 38, US38.12.

- `tenants.created_via`: `console` for tenants an operator creates from
  now on -- they never get local sign-in -- and `cli` for every tenant
  before (unchanged behaviour).
- `tenant_invitations`: the first admin's one-time link, a tenant row.
"""

import sqlalchemy as sa
from alembic import op

revision = "a0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("created_via", sa.String(), nullable=False, server_default="cli"))
    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("app_current_tenant()")),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("invited_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(), nullable=True),
        sa.Column("redeemed_by_user_id", sa.String(), nullable=True),
    )
    op.create_index("ix_tenant_invitations_tenant_id", "tenant_invitations", ["tenant_id"])
    # Privileges from f2a3b4c5d6e7's defaults; row-level security keeps tenants apart.
    op.execute("ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_invitations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_invitations "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )


def downgrade() -> None:
    op.drop_table("tenant_invitations")
    op.drop_column("tenants", "created_via")
