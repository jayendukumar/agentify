"""add the operator console and support access

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8

Epic 38, US38.8.

- Platform tables for the operator console (operators, their sessions and
  sign-ins in flight, the platform audit log). No tenant; the app role
  gets no access to them (the default privileges from f2a3b4c5d6e7 gave
  it some, so they're revoked).
- A third role, `axyntro_platform`, for the console: it may write
  `tenants` and `tenant_domains` and its own tables, and has no privilege
  on any tenant table -- so it can't read tenant data even by mistake.
  Without BYPASSRLS, like the app role.
- Tenant tables for support access (grants, one-time entries), under
  row-level security, and `sessions.support_grant_id`.
"""

import os

import sqlalchemy as sa
from alembic import op

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

OPERATOR_TABLES = ("operators", "operator_sessions", "operator_login_requests", "platform_audit_events")
SUPPORT_TABLES = ("support_grants", "support_entries")


def _app_role() -> str:
    return os.environ.get("DATABASE_APP_ROLE", "axyntro_app")


def _platform_role() -> str:
    return os.environ.get("DATABASE_PLATFORM_ROLE", "axyntro_platform")


def _tenant_column() -> sa.Column:
    return sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id"), nullable=False,
                     server_default=sa.text("app_current_tenant()"))


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "operators",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("last_sign_in_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("issuer", "subject", name="uq_operators_identity"),
    )
    op.create_table(
        "operator_sessions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("operator_id", sa.String(), sa.ForeignKey("operators.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(), nullable=False),
        sa.Column("auth_method", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("idp_id_token", sa.Text(), nullable=True),
    )
    op.create_index("ix_operator_sessions_operator_id", "operator_sessions", ["operator_id"])
    op.create_table(
        "operator_login_requests",
        sa.Column("state_hash", sa.String(), primary_key=True),
        sa.Column("nonce", sa.String(), nullable=False),
        sa.Column("code_verifier", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "platform_audit_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("operator_id", sa.String(), nullable=True),
        sa.Column("operator_name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False, server_default="success"),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_platform_audit_events_occurred_at", "platform_audit_events", ["occurred_at"])
    op.create_index("ix_platform_audit_events_tenant_id", "platform_audit_events", ["tenant_id"])

    op.create_table(
        "support_grants",
        sa.Column("id", sa.String(), primary_key=True),
        _tenant_column(),
        sa.Column("access", sa.String(), nullable=False),
        sa.Column("operator_email", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("granted_by_id", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("granted_by_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_name", sa.String(), nullable=True),
    )
    op.create_table(
        "support_entries",
        sa.Column("token_hash", sa.String(), primary_key=True),
        _tenant_column(),
        sa.Column("grant_id", sa.String(), sa.ForeignKey("support_grants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operator_id", sa.String(), nullable=False),
        sa.Column("operator_name", sa.String(), nullable=False),
        sa.Column("operator_email", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_support_entries_grant_id", "support_entries", ["grant_id"])
    app_role = _app_role()
    for table in SUPPORT_TABLES:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO "{app_role}"')
    op.add_column("sessions", sa.Column("support_grant_id", sa.String(), nullable=True))
    op.create_foreign_key("sessions_support_grant_id_fkey", "sessions", "support_grants", ["support_grant_id"], ["id"],
                          ondelete="CASCADE")
    op.create_index("ix_sessions_support_grant_id", "sessions", ["support_grant_id"])

    # The app role: nothing on the operator console's tables.
    for table in OPERATOR_TABLES:
        op.execute(f'REVOKE ALL ON {table} FROM "{app_role}"')

    # The console's role.
    role = _platform_role()
    if not bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
        password = os.environ.get("DATABASE_PLATFORM_PASSWORD")
        if password is None and os.environ.get("ENVIRONMENT", "development") != "production":
            password = "axyntro_platform"  # local development only, like the app role's
        login = "LOGIN PASSWORD '{}'".format(password.replace("'", "''")) if password else "NOLOGIN"
        op.execute(f'CREATE ROLE "{role}" {login} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
    database = bind.scalar(sa.text("SELECT current_database()"))
    op.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"')
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE ON tenants, tenant_domains TO "{role}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON operators, operator_sessions, operator_login_requests TO "{role}"')
    op.execute(f'GRANT SELECT, INSERT ON platform_audit_events TO "{role}"')


def downgrade() -> None:
    role = _platform_role()
    op.execute(f'REVOKE ALL ON tenants, tenant_domains FROM "{role}"')
    op.drop_index("ix_sessions_support_grant_id", "sessions")
    op.drop_constraint("sessions_support_grant_id_fkey", "sessions", type_="foreignkey")
    op.drop_column("sessions", "support_grant_id")
    for table in ("support_entries", "support_grants", *reversed(OPERATOR_TABLES)):
        op.drop_table(table)
    # The role stays: other databases in the cluster (the test database) may use it.
