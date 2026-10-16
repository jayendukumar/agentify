"""Epic 13, US13.3/US13.4: builds the registry connectors an agent can be
published to or searched in.

Epic 38, US38.11: registries are the tenant's own (`registries` rows, set
by its admins). Without rows, the first tenant keeps the REGISTRIES
environment variable, as before Epic 38, and any other tenant gets the
built-in local registry -- whose entries are tenant rows anyway. Built
per request, since they depend on the tenant; connectors themselves are
cheap, stateless dispatchers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import RegistryConfig, get_settings
from app.db.models import RegistryModel
from app.db.session import get_db

from .base import RegistryConnector
from .local import LocalRegistryConnector

CONNECTOR_TYPES: dict[str, type[RegistryConnector]] = {
    "local": LocalRegistryConnector,
}
DEFAULT_REGISTRIES = [RegistryConfig(name="local", type="local")]


class RegistryConfigError(Exception):
    pass


def _build_connector(config: RegistryConfig) -> RegistryConnector:
    connector_cls = CONNECTOR_TYPES.get(config.type)
    if connector_cls is None:
        raise RegistryConfigError(
            f"Unknown registry type '{config.type}' for registry '{config.name}' -- "
            f"supported types: {sorted(CONNECTOR_TYPES)}"
        )
    return connector_cls(config.name, config.type)


def configured(db: Session) -> tuple[list[RegistryConfig], str]:
    """This tenant's registries, and where they come from: `tenant` (its
    own rows), `environment` (the first tenant's REGISTRIES) or `default`."""
    rows = list(db.scalars(select(RegistryModel).order_by(RegistryModel.name)))
    if rows:
        return [RegistryConfig(name=r.name, type=r.type) for r in rows], "tenant"
    from app.tenancy.identity import current_slug

    settings = get_settings()
    if current_slug() == settings.tenant_default_slug:
        return list(settings.registries), "environment"
    return list(DEFAULT_REGISTRIES), "default"


def registries_for(db: Session) -> dict[str, RegistryConnector]:
    connectors: dict[str, RegistryConnector] = {}
    for config in configured(db)[0]:
        if config.name in connectors:
            raise RegistryConfigError(f"Duplicate registry name '{config.name}' in configuration")
        connectors[config.name] = _build_connector(config)
    return connectors


def get_registries(db: Annotated[Session, Depends(get_db, scope="function")]) -> dict[str, RegistryConnector]:
    """FastAPI dependency: the request's tenant's registries."""
    return registries_for(db)
