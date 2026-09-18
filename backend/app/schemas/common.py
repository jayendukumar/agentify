from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]
ElementType = Literal["task", "decision", "start_event", "end_event", "intermediate_event"]
ActorType = Literal["role", "system", "external_party"]


class SourceRef(BaseModel):
    document_id: str
    location: str
    excerpt: str


class Actor(BaseModel):
    id: str
    name: str
    type: ActorType


class ProcessElement(BaseModel):
    """Mirrors the canonical schema in the process-doc-ingestion skill."""

    id: str
    type: ElementType
    label: str
    actor_id: str | None = None
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    systems_touched: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    confidence: Confidence = "medium"


class ProcessFlow(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: str
    condition: str | None = None

    model_config = {"populate_by_name": True}


class ProcessSchema(BaseModel):
    process_name: str
    actors: list[Actor] = Field(default_factory=list)
    elements: list[ProcessElement] = Field(default_factory=list)
    flows: list[ProcessFlow] = Field(default_factory=list)


class Timestamped(BaseModel):
    created_at: datetime
    updated_at: datetime
