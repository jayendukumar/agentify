from __future__ import annotations

import logging
from collections.abc import Iterator
from functools import lru_cache

from fastapi import Request
from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.config import get_settings
from app.tenancy.context import current_tenant_id

logger = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_owner_engine() -> Engine:
    """Epic 38: as the tables' owner, for migrations and `python -m
    app.tenancy` -- never for serving requests."""
    settings = get_settings()
    return create_engine(settings.database_owner_url or settings.database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def _set_transaction_tenant(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
    """Epic 38, US38.3: every transaction runs as one tenant, and Postgres
    row-level security does the rest. `SET LOCAL` (set_config(..., true))
    ends with the transaction, so a pooled connection never carries a
    tenant into someone else's work -- also under a transaction-mode
    pooler. No tenant, nothing set: the transaction sees no tenant rows."""
    tenant_id = session.info.get("tenant_id") or current_tenant_id()
    if tenant_id:
        connection.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed on success,
    rolled back on any exception, always closed. Runs as the tenant the
    request's host resolved to (app/tenancy/middleware.py)."""
    tenant = getattr(request.state, "tenant", None)
    session = get_session_factory()(info={"tenant_id": tenant.id} if tenant is not None else {})
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def isolation_enforced() -> bool:
    """Epic 38, US38.2: False when the app's database role skips row-level
    security (a superuser, or BYPASSRLS) -- then nothing keeps tenants
    apart."""
    with get_engine().connect() as connection:
        bypasses = connection.scalar(
            text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    return not bypasses
