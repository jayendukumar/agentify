from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class VersionSummary(BaseModel):
    id: str
    process_id: str
    label: str | None = None
    created_at: datetime


class VersionDetail(VersionSummary):
    xml: str


class VersionDiffRequest(BaseModel):
    from_version_id: str
    to_version_id: str


class VersionDiffResult(BaseModel):
    from_version_id: str
    to_version_id: str
    added_element_ids: list[str]
    removed_element_ids: list[str]
    changed_element_ids: list[str]
