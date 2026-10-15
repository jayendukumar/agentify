"""add tenant_identity: each tenant's own IdP settings

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7

Epic 38, US38.6. A tenant table like every other (row-level security,
forced). No rows are created: without one, the first tenant keeps using
the OIDC_* environment variables.
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_identity",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                  server_default=sa.text("app_current_tenant()")),
        sa.Column("auth_mode", sa.String(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("discovery_url", sa.String(), nullable=False),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret_ref", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("acr_values", sa.String(), nullable=False),
        sa.Column("groups_claim", sa.String(), nullable=False),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False),
        sa.Column("require_mfa", sa.Boolean(), nullable=False),
        sa.Column("mfa_amr_values", sa.JSON(), nullable=False),
        sa.Column("mfa_acr_values", sa.JSON(), nullable=False),
        sa.Column("scim_link_claim", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_identity_tenant"),
    )
    op.create_index("ix_tenant_identity_tenant_id", "tenant_identity", ["tenant_id"])
    op.execute("ALTER TABLE tenant_identity ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_identity FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenant_identity "
        "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
    )
    role = os.environ.get("DATABASE_APP_ROLE", "axyntro_app")
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_identity TO "{role}"')


def downgrade() -> None:
    op.drop_table("tenant_identity")
