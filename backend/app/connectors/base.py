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
        """Secrets never sit in stored config, only a reference to one:
        `token_ref` (`secret://tenant/<name>`, in the tenant's own secrets
        store, US38.10), or for the first tenant `token_env`, an
        environment variable's name."""
        from app import secret_store
        from app.tenancy.identity import current_slug

        ref = self.config.get("token_ref") or (f"env:{self.config['token_env']}" if self.config.get("token_env") else "")
        if not ref:
            return None
        slug = current_slug()
        try:
            secret_store.check_ref(ref, slug or "", example="secret://tenant/connector_token")
        except secret_store.SecretRefError as exc:
            raise ConnectorError(f"The connector's token: {exc}") from exc
        value = secret_store.resolve(ref, slug)
        if not value:
            raise ConnectorError(f"The connector's token isn't set: the hosting company stores it as "
                                 f"{secret_store.where_to_put(ref, slug or '')}")
        return value

    @abstractmethod
    async def fetch(self) -> SyncBatch: ...
