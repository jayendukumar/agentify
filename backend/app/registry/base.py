"""Epic 13, US13.1: the registry connector interface every registry
backend implements identically -- the local reference connector
(app/registry/local.py) today, a real vendor connector later -- so the
rest of the product (search, push, health) never needs to know which
kind of registry it's talking to. See
planning/epics/13-agent-registry-connectivity.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class RegistryConnectorError(Exception):
    """Base for errors talking to a registry -- subclasses distinguish
    *why* a push/pull/search failed so the API layer (app/api/
    registries.py) can tell "the registry is unreachable" apart from
    "our credentials are bad" (US13.6), instead of one generic failure."""


class RegistryUnreachableError(RegistryConnectorError):
    pass


class RegistryAuthError(RegistryConnectorError):
    pass


@dataclass
class RegistryHealth:
    name: str
    reachable: bool
    authenticated: bool
    message: str | None = None


@dataclass
class RegistryEntry:
    id: str
    registry_name: str
    agent_name: str
    tags: list[str]
    definition: dict
    pushed_at: datetime
    source_process_id: str | None = None
    source_node_ids: list[str] = field(default_factory=list)
    pushed_by: str | None = None


class RegistryConnector(ABC):
    """`name` is this connector *instance's* configured name
    (RegistryConfig.name, US13.3), not a class-level constant -- the same
    connector type can be configured more than once under different names
    (US13.4), e.g. two local registries for two teams."""

    def __init__(self, name: str, type_: str) -> None:
        self.name = name
        self.type = type_

    @abstractmethod
    def check_health(self) -> RegistryHealth: ...

    @abstractmethod
    def push(
        self,
        *,
        agent_name: str,
        definition: dict,
        tags: list[str] | None = None,
        source_process_id: str | None = None,
        source_node_ids: list[str] | None = None,
        pushed_by: str | None = None,
    ) -> RegistryEntry: ...

    @abstractmethod
    def pull(self, entry_id: str) -> RegistryEntry | None: ...

    @abstractmethod
    def search(self, query: str) -> list[RegistryEntry]:
        """Empty query returns everything (browse mode) -- US13.5 asks for
        both browse and search, not two separate operations."""
