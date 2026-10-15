"""add SCIM provisioning and group-to-role mappings

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
"""

from alembic import op
import sqlalchemy as sa


revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("provisioned_by", sa.String(), nullable=False, server_default="local"))
    op.add_column("users", sa.Column("email", sa.String(), nullable=True))
    op.add_column("users", sa.Column("scim_user_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("scim_external_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("scim_profile", sa.JSON(), nullable=True))
    op.add_column("users", sa.Column("scim_deleted_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("idp_groups", sa.JSON(), nullable=True))
    op.create_index("ix_users_scim_user_name", "users", ["scim_user_name"])
    op.create_index("ix_users_scim_external_id", "users", ["scim_external_id"])
    # Users who signed in over SSO before this migration.
    op.execute("UPDATE users SET provisioned_by = 'sso' WHERE id IN (SELECT user_id FROM user_identities)")
    op.create_table(
        "scim_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "directory_groups",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_directory_groups_external_id", "directory_groups", ["external_id"])
    op.create_table(
        "directory_group_members",
        sa.Column("group_id", sa.String(), sa.ForeignKey("directory_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_directory_group_members_user_id", "directory_group_members", ["user_id"])
    op.create_table(
        "group_role_mappings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("group", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False, server_default="organization"),
        sa.Column("scope_id", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.UniqueConstraint("group", "role", "scope_type", "scope_id"),
    )
    op.create_index("ix_group_role_mappings_group", "group_role_mappings", ["group"])


def downgrade() -> None:
    op.drop_index("ix_group_role_mappings_group", table_name="group_role_mappings")
    op.drop_table("group_role_mappings")
    op.drop_index("ix_directory_group_members_user_id", table_name="directory_group_members")
    op.drop_table("directory_group_members")
    op.drop_index("ix_directory_groups_external_id", table_name="directory_groups")
    op.drop_table("directory_groups")
    op.drop_table("scim_tokens")
    op.drop_index("ix_users_scim_external_id", table_name="users")
    op.drop_index("ix_users_scim_user_name", table_name="users")
    for column in ("idp_groups", "scim_deleted_at", "scim_profile", "scim_external_id", "scim_user_name", "email",
                   "provisioned_by"):
        op.drop_column("users", column)
