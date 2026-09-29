"""Epic 22: per-run execution snapshots (US22.1/US22.2) and the three
reproducibility tiers a snapshot does and doesn't guarantee (US22.3).

The snapshot is deliberately a free-form `dict` (built by
`app/twin/snapshot.py::build_run_snapshot`), not a fully-typed nested
schema -- it's a point-in-time capture of several other schemas' current
values (AgentDefinition's governance fields, a TwinEnvironment), and
duplicating those as a second parallel type here would drift the moment
either source schema changes. `RunSnapshotView` is what the API actually
returns: the stored snapshot plus the three fixed tier labels/explanations
this epic's requirements call for.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ReproducibilityTier = Literal["guaranteed", "best_effort", "not_guaranteed"]

# US22.3: fixed, not data-driven -- these three sentences are the whole
# point of the feature (never let "we can reconstruct this run's config"
# be misread as "re-running it reproduces the same transcript").
REPRODUCIBILITY_EXPLANATIONS: dict[str, str] = {
    "configuration": (
        "Guaranteed: every versioned component this run used (agent version, model policy, tool "
        "contracts, guardrails, escalation policy, knowledge sources, twin environment/dataset "
        "versions, scenario id) is recorded below exactly as it was at run time."
    ),
    "behavioral": (
        "Best-effort: re-running this exact configuration will plausibly behave similarly, but nothing "
        "about a real LLM call is assumed deterministic -- tool call order, wording, and intermediate "
        "decisions can differ."
    ),
    "exact_output": (
        "Not guaranteed: the exact transcript/output of this run is not reproducible, even against the "
        "identical configuration below."
    ),
}


class RunSnapshotView(BaseModel):
    run_id: str
    snapshot: dict[str, Any] | None
    reproducibility: dict[str, ReproducibilityTier] = {
        "configuration": "guaranteed",
        "behavioral": "best_effort",
        "exact_output": "not_guaranteed",
    }
    explanation: dict[str, str] = REPRODUCIBILITY_EXPLANATIONS
