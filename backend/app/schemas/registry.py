from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RegistryStatus(BaseModel):
    name: str
    type: str
    reachable: bool
    authenticated: bool
    message: str | None = None


class RegistryEntryOut(BaseModel):
    id: str
    registry_name: str
    agent_name: str
    tags: list[str]
    definition: dict
    pushed_at: datetime
    source_process_id: str | None = None
    source_node_ids: list[str] = []
    pushed_by: str | None = None
    pushed_by_name: str | None = None


class RegistrySearchResult(BaseModel):
    entries: list[RegistryEntryOut]
    # Per-registry search failures (US13.6) -- kept separate from `entries`
    # so "no matches" and "a registry was unreachable" are never conflated
    # into the same empty-list result.
    registry_errors: dict[str, str] = {}


class RegistryPushRequest(BaseModel):
    agent_name: str
    definition: dict
    tags: list[str] = []
    source_process_id: str | None = None
    source_node_ids: list[str] = []
