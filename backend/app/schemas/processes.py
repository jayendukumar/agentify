from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .common import ProcessSchema


class ProcessCreateRequest(BaseModel):
    name: str


class ProcessSummary(BaseModel):
    id: str
    name: str
    document_count: int
    has_draft_bpmn: bool
    finalized_version_count: int
    created_at: datetime
    updated_at: datetime


class ProcessDetail(ProcessSummary):
    process_schema: ProcessSchema | None = None
