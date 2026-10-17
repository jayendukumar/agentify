"""Epic 38, US38.13: an admin's export of everything the organization holds."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TenantExportOut(BaseModel):
    id: str
    # queued | running | ready | downloaded | failed | expired
    status: str
    requested_by_name: str
    created_at: datetime
    finished_at: datetime | None = None
    downloaded_at: datetime | None = None
    downloaded_by_name: str | None = None
    size_bytes: int | None = None
    file_count: int | None = None
    rows: int | None = None
    error: str | None = None
    # When a ready archive is removed if nobody downloads it.
    expires_at: datetime | None = None
