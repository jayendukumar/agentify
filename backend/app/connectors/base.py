"""Epic 31, US31.2: the connector contract.

A connector reads a system of record and yields `SyncItem`s -- context
entities (by kind, with the source's own id) or policy documents. It never
writes back: every connector here only issues reads (HTTP GET, MCP
`tools/call` of a read tool, filesystem reads), and the product is never
the system of record for policies, applications, or identities.

`app/connectors/sync.py` applies the items: create, update in place
(bumping versions, so Epic 28 staleness and impact analysis see the
change), or retire what the source no longer has.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.schemas.context import AppliesTo


class ConnectorError(Exception):
    pass


@dataclass
class SyncItem:
    kind: str
    external_id: str
    name: str
    code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    applies_to: AppliesTo = field(default_factory=AppliesTo)
    # For kind="policy_document": the file to ingest through Epic 27.
    content: bytes | None = None
    filename: str | None = None
    content_type: str | None = None


@dataclass
class SyncBatch:
    items: list[SyncItem]
    # True when `items` is everything the source has for the kinds it
    # returned, so anything this connector synced before and didn't see now
    # is retired. False for incremental/partial reads.
    complete: bool = True


class Connector(ABC):
    type: str = ""
    description: str = ""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def secret(self) -> str | None:
        """Secrets come from the environment, never from stored config."""
        name = self.config.get("token_env")
        if not name:
            return None
        value = os.environ.get(name)
        if not value:
            raise ConnectorError(f"Environment variable '{name}' (the connector's token) is not set")
        return value

    @abstractmethod
    async def fetch(self) -> SyncBatch: ...
