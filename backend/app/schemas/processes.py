from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .common import ProcessSchema
from .context import ProcessScope


class ProcessCreateRequest(BaseModel):
    name: str
    # Epic 27, US27.2: optional at creation; organization-wide if omitted.
    scope: ProcessScope | None = None


class ProcessSummary(BaseModel):
    id: str
    name: str
    document_count: int
    has_draft_bpmn: bool
    finalized_version_count: int
    created_at: datetime
    updated_at: datetime
    scope: ProcessScope = ProcessScope()


class ProcessDetail(ProcessSummary):
    process_schema: ProcessSchema | None = None
