"""add identity: scoped role grants, contributions, approvals, audit events, baseline approval

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

# Epic 37, US37.1: what each pre-Epic-37 role becomes. An editor did
# everything an analyst, architect and process owner now do; nobody was an
# approver before, so approvals have to be granted deliberately.
_EDITOR_ROLES = ("analyst", "architect", "process_owner")


def upgrade() -> None:
    op.add_column("users", sa.Column("actor_type", sa.String(), nullable=False, server_default="user"))
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(), primary_key=True),
        sa.Column("scope_type", sa.String(), primary_key=True, server_default="organization"),
        sa.Column("scope_id", sa.String(), primary_key=True, server_default=""),
        sa.Column("source", sa.String(), nullable=False, server_default="local"),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("granted_by", sa.String(), nullable=True),
    )
    for role in _EDITOR_ROLES:
        op.execute(
            "INSERT INTO user_roles (user_id, role, scope_type, scope_id, source) "
            f"SELECT id, '{role}', 'organization', '', 'local' FROM users WHERE role = 'editor'"
        )
    op.drop_column("users", "role")

    op.create_table(
        "contributions",
        sa.Column("seq", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("object_type", sa.String(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=True),
        sa.Column("actor_type", sa.String(), nullable=False, server_default="user"),
        sa.Column("actor_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_contributions_object", "contributions", ["object_type", "object_id", "seq"])
    op.create_index("ix_contributions_process_id", "contributions", ["process_id"])

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("object_type", sa.String(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("process_id", sa.String(), nullable=True),
        sa.Column("through_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("approver_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_approvals_object_id", "approvals", ["object_id"])
    op.create_index("ix_approvals_process_id", "approvals", ["process_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("actor_name", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=True),
        sa.Column("object_id", sa.String(), nullable=True),
        sa.Column("process_id", sa.String(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False, server_default="success"),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_process_id", "audit_events", ["process_id"])

    # US37.4: existing baselines were made current without an approval step;
    # they stay usable (grandfathered) rather than blocking every process.
    op.add_column("versions", sa.Column("status", sa.String(), nullable=False, server_default="approved"))
    op.add_column("versions", sa.Column("contribution_seq", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("versions", sa.Column("decided_at", sa.DateTime(), nullable=True))
    op.add_column(
        "versions", sa.Column("decided_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    )
    op.add_column("agent_artifacts", sa.Column("approved_through_seq", sa.BigInteger(), nullable=True))
    op.add_column("organizations", sa.Column("governance", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("organizations", "governance")
    op.drop_column("agent_artifacts", "approved_through_seq")
    op.drop_column("versions", "decided_by")
    op.drop_column("versions", "decided_at")
    op.drop_column("versions", "contribution_seq")
    op.drop_column("versions", "status")
    op.drop_table("audit_events")
    op.drop_table("approvals")
    op.drop_table("contributions")
    op.add_column("users", sa.Column("role", sa.String(), nullable=False, server_default="viewer"))
    op.execute(
        "UPDATE users SET role = 'editor' WHERE id IN "
        "(SELECT user_id FROM user_roles WHERE role IN ('analyst', 'architect', 'process_owner'))"
    )
    op.drop_table("user_roles")
    op.drop_column("users", "active")
    op.drop_column("users", "actor_type")
