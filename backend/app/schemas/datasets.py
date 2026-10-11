"""Epic 32: labelled datasets and ground truth -- see
planning/epics/32-labelled-datasets-and-ground-truth.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Provenance = Literal["historical", "synthetic", "expert", "oracle"]


Decision = Literal["approve", "reject", "escalate"]


class RowLabels(BaseModel):
    """Expected results for one case. Partial by design: only labelled
    fields are graded (`grade_run` compares key by key). `decision` is the
    case's expected outcome, graded against the agent's decision output
    field (an escalated run counts as "escalate")."""

    outputs: dict[str, Any] = {}
    decision: Decision | None = None


class DatasetRowInput(BaseModel):
    inputs: dict[str, Any] = {}
    labels: RowLabels = RowLabels()
    segment: str = "typical"
    provenance: Provenance = "expert"
    tags: list[str] = []
    critical: bool = False
    row_key: str | None = None


class DatasetRowUpdate(BaseModel):
    inputs: dict[str, Any] | None = None
    labels: RowLabels | None = None
    segment: str | None = None
    tags: list[str] | None = None
    critical: bool | None = None
    confirm: bool = False


class AmbiguityUpdate(BaseModel):
    """US32.5."""

    ambiguous: bool
    note: str | None = None
    second_labels: RowLabels | None = None


class DatasetRow(BaseModel):
    row_key: str
    revision_id: str
    valid_from_version: int
    inputs: dict[str, Any]
    labels: RowLabels | None = None  # hidden for holdout rows unless asked (US32.8)
    segment: str
    provenance: Provenance
    tags: list[str] = []
    critical: bool = False
    ambiguous: bool = False
    ambiguity_note: str | None = None
    second_labels: RowLabels | None = None
    holdout: bool = False
    source_run_id: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    agent_artifact_id: str | None = None


class DatasetSummary(BaseModel):
    id: str
    process_id: str
    agent_artifact_id: str | None = None
    name: str
    description: str = ""
    version: int
    row_count: int = 0
    holdout_count: int = 0
    ambiguous_count: int = 0
    confirmed_count: int = 0
    segments: dict[str, int] = {}
    provenance: dict[str, int] = {}
    agreement_rate: float | None = None
    created_at: datetime
    updated_at: datetime


class DatasetRowPage(BaseModel):
    version: int
    total: int
    rows: list[DatasetRow]


class RowChange(BaseModel):
    row_key: str
    change: Literal["added", "removed", "relabelled", "edited"]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class DatasetDiff(BaseModel):
    from_version: int
    to_version: int
    added: int = 0
    removed: int = 0
    relabelled: int = 0
    edited: int = 0
    changes: list[RowChange] = []


ColumnRole = Literal["input", "label", "decision", "segment", "tag", "ignore"]
PseudonymMode = Literal["keep", "hash", "fake"]


class ColumnMapping(BaseModel):
    """US32.3: one source column's role. `field` is the agent input field
    or labelled output field it maps to."""

    column: str
    role: ColumnRole
    field: str | None = None
    pseudonymize: PseudonymMode = "keep"


class ImportPreview(BaseModel):
    columns: list[str]
    suggested: list[ColumnMapping]
    sample: list[dict[str, Any]] = []
    total_rows: int = 0
    mapped_sample: list[DatasetRowInput] = []


class ImportResult(BaseModel):
    version: int
    added: int
    skipped: int = 0


class HoldoutRequest(BaseModel):
    fraction: float = Field(default=0.2, gt=0, lt=1)
    seed: int = 7


class PromoteRunRequest(BaseModel):
    """US32.7: turn a run into a golden case, with the correct answer."""

    run_id: str
    labels: RowLabels
    segment: str = "from-failures"
    critical: bool = False
