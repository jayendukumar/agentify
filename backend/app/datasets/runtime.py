"""Epic 32, US32.2: turn a data-driven scenario plus one dataset row into
the scenario a single run executes -- the row's inputs, graded against the
row's own labels, with the scenario's test logic (stubs, reviewer, faults,
step matching) unchanged."""

from __future__ import annotations

from typing import Any

from app.db.models import DatasetRowModel
from app.schemas.twin import TwinScenario

_ESCALATION_WORDS = ("escalat", "refer", "manual review")


def apply_dataset_row(scenario: TwinScenario, row: DatasetRowModel) -> TwinScenario:
    labels = row.labels or {}
    outputs: dict[str, Any] = dict(labels.get("outputs") or {})
    return scenario.model_copy(
        update={
            "inputs": dict(row.inputs or {}),
            # Labelled outputs replace the scenario's own; unlabelled fields
            # aren't graded (partial labels, US32.1 / Epic 32 "Why").
            "expected_outputs": outputs,
            "expected_decision": labels.get("decision"),
        }
    )


def decision_from_output(final_output: dict[str, Any] | None, field: str) -> str | None:
    from .service import normalize_decision

    if not final_output:
        return None
    value = final_output.get(field)
    decision = normalize_decision(value)
    if decision is None and isinstance(value, str) and any(w in value.lower() for w in _ESCALATION_WORDS):
        return "escalate"
    return decision
