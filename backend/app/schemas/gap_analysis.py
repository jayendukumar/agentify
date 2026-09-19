from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .chat import DiagramDiff

GapKind = Literal["structural", "cross_document"]
GapFindingStatus = Literal["open", "resolved", "dismissed"]


class GapFindingOption(BaseModel):
    label: str
    # None only for an LLM-authored "no clean automatic fix, needs a human
    # decision" option -- "Dismiss" is not one of these; it's a status any
    # finding can always move to regardless of what the LLM proposed (see
    # GapFindingModel's docstring).
    diff: DiagramDiff | None = None


class GapFinding(BaseModel):
    id: str
    process_id: str
    kind: GapKind
    question: str
    target_element_ids: list[str]
    options: list[GapFindingOption]
    status: GapFindingStatus
    chosen_option_label: str | None = None
    created_at: datetime
    decided_at: datetime | None = None


class GapFindingResolveRequest(BaseModel):
    option_index: int
