"""Epic 13, US13.3/US13.4: builds the configured set of registry
connectors from Settings.registries. Cached (like app/db/session.py's
get_engine/get_session_factory, app/llm/client.py's get_llm_client) since
connectors are stateless dispatchers, not per-request objects.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import RegistryConfig, get_settings

from .base import RegistryConnector
from .local import LocalRegistryConnector

_CONNECTOR_TYPES: dict[str, type[RegistryConnector]] = {
    "local": LocalRegistryConnector,
}


class RegistryConfigError(Exception):
    pass


def _build_connector(config: RegistryConfig) -> RegistryConnector:
    connector_cls = _CONNECTOR_TYPES.get(config.type)
    if connector_cls is None:
        raise RegistryConfigError(
            f"Unknown registry type '{config.type}' for registry '{config.name}' -- "
            f"supported types: {sorted(_CONNECTOR_TYPES)}"
        )
    return connector_cls(config.name, config.type)


@lru_cache
def get_registries() -> dict[str, RegistryConnector]:
    connectors: dict[str, RegistryConnector] = {}
    for config in get_settings().registries:
        if config.name in connectors:
            raise RegistryConfigError(f"Duplicate registry name '{config.name}' in configuration")
        connectors[config.name] = _build_connector(config)
    return connectors


def get_registry(name: str) -> RegistryConnector | None:
    return get_registries().get(name)
