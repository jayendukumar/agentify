from logging.config import fileConfig

from sqlalchemy import create_engine, engine_from_config, inspect
from sqlalchemy import pool

from alembic import context

from app.config import get_settings
from app.db.models import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# Not when called from the app (Epic 38, US38.14: migrating a tenant's own
# database), whose logging is already set up.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# Read the DB URL from app.config.Settings (.env) rather than duplicating it
# as a static value in alembic.ini -- one source of truth for credentials.
# Epic 38: migrations run as the tables' owner, not the app role the API
# uses (which can't create tables and is subject to row-level security).
_settings = get_settings()
# US38.14: `database_url` in the config's attributes migrates that one
# database (a tenant's own, from app/tenancy/databases.py) and nothing else.
_single_url = config.attributes.get("database_url")
config.set_main_option(
    "sqlalchemy.url", (_single_url or _settings.database_owner_url or _settings.database_url).replace("%", "%%")
)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

        tenant_databases = [] if _single_url else _tenant_databases(connection)

    # Epic 38, US38.14: the same migrations against every tenant database,
    # so the schema never differs between a tenant's own and the shared one.
    for slug, url in tenant_databases:
        print(f"Migrating tenant {slug}'s own database")
        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


def _tenant_databases(connection) -> list[tuple[str, str]]:
    from app.tenancy.databases import owner_url, tenant_databases

    if not inspect(connection).has_table("tenants") or "database_ref" not in {
        c["name"] for c in inspect(connection).get_columns("tenants")
    }:
        return []
    return [(slug, owner_url(ref, slug)) for slug, ref in tenant_databases(connection)]


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
