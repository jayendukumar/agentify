from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class BPMNGenerateRequest(BaseModel):
    document_ids: list[str] | None = None  # None = use all documents attached to the process


class BPMNDocument(BaseModel):
    process_id: str
    xml: str
    low_confidence_element_ids: list[str] = []
    # US3.4: structural issues (app/bpmn/validation.py) in the current
    # draft -- e.g. a node left disconnected by an imperfect multi-document
    # merge. Surfaced, not blocking: a generated draft is meant to be
    # reviewed/fixed (chat editing, Epic 5), not withheld because real
    # extracted data is imperfect. Always [] for a manually-edited draft
    # (PUT rejects invalid XML outright instead -- see app/api/bpmn.py).
    validation_issues: list[str] = []
    generated_at: datetime


class BPMNUpdateRequest(BaseModel):
    xml: str
