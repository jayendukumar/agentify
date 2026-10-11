"""Epic 30, US30.6/US30.7: control coverage and compliance evidence for one
agent -- which applicable, testable controls have a passing twin
situation on the agent's *current* version and pinned Context Pack.

Evidence is decision support, not certification: it shows the simulated
agent behaved as each rule requires in the tested situations, nothing
more. Every view and export carries that disclaimer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AgentArtifactModel, ContextEntityModel, ContextPackModel, SimulationRunModel, TwinScenarioModel
from app.ids import utcnow

from .resolver import is_pack_stale

DISCLAIMER = (
    "Decision support, not certification: this evidence shows that the simulated agent behaved as each rule "
    "requires in the situations tested. It is not a guarantee of compliance."
)

EvidenceStatus = Literal["evidenced", "out_of_date", "failing", "missing"]


class SituationEvidence(BaseModel):
    scenario_id: str
    scenario_name: str
    run_id: str | None = None
    run_at: datetime | None = None
    passed: bool | None = None
    agent_version: int | None = None
    context_pack_id: str | None = None
    current: bool = False


class ControlEvidence(BaseModel):
    target_id: str
    control_id: str
    code: str | None = None
    name: str
    version: int
    status: EvidenceStatus
    situations: list[SituationEvidence] = []


class ComplianceEvidence(BaseModel):
    artifact_id: str
    agent_name: str
    agent_version: int
    context_pack_id: str | None = None
    context_stale: bool = False
    pinned_versions: list[dict[str, object]] = []
    controls: list[ControlEvidence] = []
    evidenced: int = 0
    total: int = 0
    disclaimer: str = DISCLAIMER
    generated_at: datetime


def testable_controls(session: Session, artifact: AgentArtifactModel) -> list[ContextEntityModel]:
    """The controls this agent was built to follow (Epic 28 records them
    in its knowledge sources) that a twin can actually exercise."""
    controls = []
    for source in (artifact.definition or {}).get("knowledge_sources", []):
        if source.get("kind") != "control":
            continue
        entity = session.get(ContextEntityModel, source.get("id"))
        if entity is None or entity.status != "confirmed" or not (entity.data or {}).get("testable", True):
            continue
        if entity not in controls:
            controls.append(entity)
    return controls


def _run_snapshot_for(run: SimulationRunModel, artifact_id: str) -> dict:
    for item in (run.snapshot or {}).get("agent_artifacts", []):
        if item.get("agent_artifact_id") == artifact_id:
            return item
    return {}


def compliance_evidence(session: Session, artifact: AgentArtifactModel) -> ComplianceEvidence:
    stale = is_pack_stale(session, artifact.context_pack_id)
    scenarios = list(
        session.scalars(select(TwinScenarioModel).where(TwinScenarioModel.agent_artifact_id == artifact.id))
    )
    runs = list(
        session.scalars(
            select(SimulationRunModel)
            .where(SimulationRunModel.agent_artifact_id == artifact.id)
            .order_by(SimulationRunModel.started_at.desc())
        )
    )
    latest_run: dict[str, SimulationRunModel] = {}
    for run in runs:
        latest_run.setdefault(run.scenario_id, run)

    controls: list[ControlEvidence] = []
    for control in testable_controls(session, artifact):
        target_id = f"ctl:{control.id}"
        situations = []
        for scenario in scenarios:
            story = scenario.story or {}
            asserted = {a.get("control_id") for a in (story.get("expect") or {}).get("compliance", [])}
            if target_id not in (story.get("covers") or []) and target_id not in asserted:
                continue
            run = latest_run.get(scenario.id)
            snap = _run_snapshot_for(run, artifact.id) if run is not None else {}
            current = (
                run is not None
                and snap.get("agent_version") == artifact.version
                and snap.get("context_pack_id") == artifact.context_pack_id
                and not stale
            )
            situations.append(
                SituationEvidence(
                    scenario_id=scenario.id,
                    scenario_name=scenario.name,
                    run_id=run.id if run else None,
                    run_at=run.started_at if run else None,
                    passed=run.graded_passed if run else None,
                    agent_version=snap.get("agent_version"),
                    context_pack_id=snap.get("context_pack_id"),
                    current=current,
                )
            )
        if any(s.current and s.passed for s in situations):
            status: EvidenceStatus = "evidenced"
        elif any(s.passed for s in situations):
            status = "out_of_date"
        elif any(s.run_id for s in situations):
            status = "failing"
        else:
            status = "missing"
        controls.append(
            ControlEvidence(
                target_id=target_id,
                control_id=control.id,
                code=control.code,
                name=control.name,
                version=control.version,
                status=status,
                situations=situations,
            )
        )

    pinned: list[dict[str, object]] = []
    pack = session.get(ContextPackModel, artifact.context_pack_id) if artifact.context_pack_id else None
    if pack is not None:
        pinned = [
            {"name": e.get("code") or e["name"], "kind": e["kind"], "version": e["version"]}
            for e in pack.entries
            if e["kind"] in ("control", "obligation", "regulation")
        ]
        seen = set()
        for passage in pack.passages:
            key = (passage.get("policy_entity_id"), passage.get("policy_version"))
            policy = session.get(ContextEntityModel, passage.get("policy_entity_id")) if passage.get("policy_entity_id") else None
            if policy is not None and key not in seen:
                seen.add(key)
                pinned.append({"name": policy.code or policy.name, "kind": "policy", "version": passage.get("policy_version")})

    definition = artifact.definition or {}
    return ComplianceEvidence(
        artifact_id=artifact.id,
        agent_name=definition.get("name", artifact.id),
        agent_version=artifact.version,
        context_pack_id=artifact.context_pack_id,
        context_stale=stale,
        pinned_versions=pinned,
        controls=controls,
        evidenced=sum(1 for c in controls if c.status == "evidenced"),
        total=len(controls),
        generated_at=utcnow(),
    )


def unevidenced_controls(session: Session, artifact: AgentArtifactModel) -> list[ControlEvidence]:
    """US30.6: what stands between this agent and RELEASE_CANDIDATE."""
    return [c for c in compliance_evidence(session, artifact).controls if c.status != "evidenced"]


_STATUS_TEXT = {
    "evidenced": "Evidenced",
    "out_of_date": "Out of date -- passed on an earlier agent version or before a rule changed; re-run",
    "failing": "Failing -- the latest run did not behave as the rule requires",
    "missing": "Not tested -- no situation exercises this rule yet",
}


def evidence_markdown(evidence: ComplianceEvidence, *, process_name: str) -> str:
    """US30.7: the export, ready to drop into an audit file."""
    lines = [
        f"# Compliance evidence -- {evidence.agent_name}",
        "",
        f"Process: {process_name}  ",
        f"Agent version: v{evidence.agent_version}  ",
        f"Context pack: {evidence.context_pack_id or 'none'}{' (rules have changed since -- evidence out of date)' if evidence.context_stale else ''}  ",
        f"Generated: {evidence.generated_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"> {evidence.disclaimer}",
        "",
        f"**{evidence.evidenced} of {evidence.total} rules evidenced.**",
        "",
    ]
    if evidence.pinned_versions:
        lines += ["## Rule and policy versions the agent was built and tested against", ""]
        lines += [f"- {p['name']} ({p['kind']}) v{p['version']}" for p in evidence.pinned_versions]
        lines.append("")
    lines += ["## Rules", ""]
    for control in evidence.controls:
        lines.append(f"### {control.code + ': ' if control.code else ''}{control.name} (v{control.version})")
        lines.append("")
        lines.append(f"Status: {_STATUS_TEXT[control.status]}")
        lines.append("")
        for s in control.situations:
            outcome = "passed" if s.passed else ("failed" if s.passed is False else "not run")
            when = f"{s.run_at:%Y-%m-%d %H:%M}" if s.run_at else "--"
            version = f"agent v{s.agent_version}" if s.agent_version else "--"
            lines.append(f"- {s.scenario_name}: {outcome}, {when}, {version}{'' if s.current else ' (not current)'}")
        if not control.situations:
            lines.append("- No situations yet.")
        lines.append("")
    return "\n".join(lines)
