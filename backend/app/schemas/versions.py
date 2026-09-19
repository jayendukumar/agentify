from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class VersionSummary(BaseModel):
    id: str
    process_id: str
    label: str | None = None
    created_at: datetime
    created_by: str | None = None
    # Denormalized display name (resolved via a join at read time, not
    # trusted from anywhere else) -- created_by alone is a users.id, not
    # something to show a Process Analyst in the version history list.
    created_by_name: str | None = None


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
    # Human-readable labels for every id above (value from the "to" version,
    # falling back to the "from" version for removed ids) -- raw ids like
    # "Task_c" aren't useful in a diff view meant for a Process Analyst.
    labels: dict[str, str | None] = {}
