from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

IngestionStatus = Literal["queued", "processing", "done", "failed"]


class DocumentSummary(BaseModel):
    id: str
    process_id: str
    filename: str
    content_type: str
    size_bytes: int
    status: IngestionStatus
    process_definition_confidence: int | None = None
    validation_message: str | None = None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    error_message: str | None = None
