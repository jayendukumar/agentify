"""add tenants, put every row in one, and turn on row-level security

Revision ID: f2a3b4c5d6e7
Revises: e0f1a2b3c4d5

Epic 38, US38.1-38.4. Everything already in the database becomes the first
tenant (TENANT_DEFAULT_SLUG), reached at TENANT_DEFAULT_HOSTS and the hosts
of FRONTEND_URL, API_PUBLIC_URL and OIDC_REDIRECT_URI. Then, table by
table: a tenant_id column, backfilled, NOT NULL, defaulting to the
transaction's tenant, and a row-level security policy that is enabled and
forced. Finally the app role the API connects as: no BYPASSRLS, owns
nothing, may read but not write the platform tables.

Runs as the tables' owner (DATABASE_OWNER_URL), which must be allowed to
create roles the first time.
"""

import os
import uuid
from urllib.parse import urlparse

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None

PLATFORM_TABLES = ("tenants", "tenant_domains")

# Uniqueness that was deployment-wide and becomes per tenant (US38.2).
PER_TENANT_UNIQUE = [
    ("users", "users_name_key", "uq_users_tenant_name", ["name"]),
    ("connectors", "connectors_name_key", "uq_connectors_tenant_name", ["name"]),
    ("context_packs", "context_packs_content_hash_key", "uq_context_packs_tenant_hash", ["content_hash"]),
    (
        "group_role_mappings",
        "group_role_mappings_group_role_scope_type_scope_id_key",
        "uq_group_role_mappings_tenant",
        ["group", "role", "scope_type", "scope_id"],
    ),
]


def _app_role() -> str:
    return os.environ.get("DATABASE_APP_ROLE", "axyntro_app")


def _tenant_tables(bind) -> list[str]:
    rows = bind.execute(
        sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
            "AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version' ORDER BY table_name"
        )
    )
    return [name for (name,) in rows if name not in PLATFORM_TABLES]


def _default_hosts() -> list[str]:
    from app.config import get_settings
    from app.tenancy.hosts import normalize_host

    settings = get_settings()
    hosts = [h for h in settings.tenant_default_hosts.split(",") if h.strip()]
    for url in (settings.frontend_url, settings.api_public_url, settings.oidc_redirect_uri):
        if urlparse(url).hostname:
            hosts.append(urlparse(url).hostname)
    return sorted({normalize_host(h) for h in hosts})


def upgrade() -> None:
    from app.config import get_settings

    settings = get_settings()
    bind = op.get_bind()
    tables = _tenant_tables(bind)

    op.execute(
        "CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS text LANGUAGE sql STABLE PARALLEL SAFE AS "
        "$$ SELECT NULLIF(current_setting('app.tenant_id', true), '') $$"
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "tenant_domains",
        sa.Column("host", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tenant_domains_tenant_id", "tenant_domains", ["tenant_id"])

    # US38.4: today's data becomes the first tenant.
    tenant_id = f"ten_{uuid.uuid4().hex[:12]}"
    org_name = bind.scalar(sa.text("SELECT name FROM organizations ORDER BY created_at LIMIT 1"))
    display_name = settings.tenant_default_name or org_name or settings.tenant_default_slug.title()
    bind.execute(
        sa.text("INSERT INTO tenants (id, slug, display_name) VALUES (:id, :slug, :name)"),
        {"id": tenant_id, "slug": settings.tenant_default_slug, "name": display_name},
    )
    for host in _default_hosts():
        bind.execute(
            sa.text("INSERT INTO tenant_domains (host, tenant_id) VALUES (:host, :tenant_id)"),
            {"host": host, "tenant_id": tenant_id},
        )

    for table in tables:
        op.execute(f'ALTER TABLE "{table}" ADD COLUMN tenant_id varchar')
        bind.execute(sa.text(f'UPDATE "{table}" SET tenant_id = :tenant_id'), {"tenant_id": tenant_id})
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN tenant_id SET NOT NULL, '
            f"ALTER COLUMN tenant_id SET DEFAULT app_current_tenant()"
        )
        op.execute(f'ALTER TABLE "{table}" ADD FOREIGN KEY (tenant_id) REFERENCES tenants (id)')
        op.execute(f'CREATE INDEX "ix_{table}_tenant_id" ON "{table}" (tenant_id)')
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY tenant_isolation ON "{table}" '
            f"USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant())"
        )

    for table, old, new, columns in PER_TENANT_UNIQUE:
        op.drop_constraint(old, table, type_="unique")
        op.create_unique_constraint(new, table, ["tenant_id", *columns])
    op.drop_constraint("user_identities_pkey", "user_identities", type_="primary")
    op.create_primary_key("user_identities_pkey", "user_identities", ["tenant_id", "issuer", "subject"])

    # US38.2: the role the API and worker connect as.
    role = _app_role()
    if not bind.scalar(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}):
        password = os.environ.get("DATABASE_APP_PASSWORD")
        if password is None and settings.environment != "production":
            password = "axyntro_app"  # local development only, like the rest of docker-compose.yml
        login = "LOGIN PASSWORD '{}'".format(password.replace("'", "''")) if password else "NOLOGIN"
        op.execute(f'CREATE ROLE "{role}" {login} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
    database = bind.scalar(sa.text("SELECT current_database()"))
    op.execute(f'GRANT CONNECT ON DATABASE "{database}" TO "{role}"')
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    for table in tables:
        op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{table}" TO "{role}"')
    for table in PLATFORM_TABLES:
        op.execute(f'GRANT SELECT ON "{table}" TO "{role}"')
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
    # Tables added by later migrations: the schema test (tests/test_tenancy.py)
    # makes sure each one is a tenant table with its policy.
    op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"')
    op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "{role}"')


def downgrade() -> None:
    """Back to one deployment-wide data set. Refuses if there's more than one
    tenant: their data can't be merged safely."""
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM tenants")) > 1:
        raise RuntimeError("More than one tenant exists; export and remove the others before downgrading")
    role = _app_role()
    op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{role}"')
    op.execute(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM "{role}"')

    op.drop_constraint("user_identities_pkey", "user_identities", type_="primary")
    op.create_primary_key("user_identities_pkey", "user_identities", ["issuer", "subject"])
    for table, old, new, columns in PER_TENANT_UNIQUE:
        op.drop_constraint(new, table, type_="unique")
        op.create_unique_constraint(old, table, columns)
    for table in _tenant_tables(bind):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DROP COLUMN tenant_id')
    op.drop_table("tenant_domains")
    op.drop_table("tenants")
    op.execute("DROP FUNCTION app_current_tenant()")
