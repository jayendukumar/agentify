"""Epic 22, US22.1: assembles the per-run execution snapshot -- every
versioned component that could have influenced a run's outcome, captured
at the moment the run starts (or, for an orchestration run, the moment its
node artifacts are resolved -- see app/db/repository.py's two call sites).

Deliberately a plain dict, not a fully-typed nested schema -- see
app/schemas/reproducibility.py's module docstring for why.
"""

from __future__ import annotations

from typing import Any, Literal

from app.db.models import AgentArtifactModel, TwinEnvironmentModel


def _artifact_snapshot(artifact: AgentArtifactModel) -> dict[str, Any]:
    definition = artifact.definition
    return {
        "agent_artifact_id": artifact.id,
        "agent_version": artifact.version,
        "primary_node_id": artifact.primary_node_id,
        "model_policy": definition.get("model_policy"),
        "tool_contracts": definition.get("tool_contracts", []),
        "guardrails": definition.get("guardrails"),
        "escalation_policy": definition.get("escalation_policy"),
        "knowledge_sources": definition.get("knowledge_sources", []),
        # Epic 28, US28.2: which organization context the agent was built with.
        "context_pack_id": artifact.context_pack_id,
    }


def _environment_snapshot(environment: TwinEnvironmentModel) -> dict[str, Any]:
    return {
        "twin_environment_id": environment.id,
        "twin_environment_version": environment.version,
        # US22.1 asks for "dataset version" -- today's data model doesn't
        # version datasets/personas independently of their owning
        # environment (see planning/decision-log.md's 2026-09-29 Epic 21
        # entry), so the environment's own version is the honest proxy for
        # both, not a fabricated per-dataset number.
        "dataset_names": sorted(environment.datasets),
        "persona_names": sorted(environment.personas),
        "dataset_versions_note": (
            "Datasets/personas aren't independently versioned -- the twin_environment_version above is "
            "their proxy."
        ),
    }


def build_run_snapshot(
    *,
    kind: Literal["twin", "orchestration"],
    scenario_id: str,
    agent_artifacts: list[AgentArtifactModel],
    environment: TwinEnvironmentModel | None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "scenario_id": scenario_id,
        "agent_artifacts": [_artifact_snapshot(a) for a in agent_artifacts],
        "twin_environment": _environment_snapshot(environment) if environment is not None else None,
    }
