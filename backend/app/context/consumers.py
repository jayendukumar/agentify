"""Epic 28, US28.5/US28.6/US28.8: what the pipeline stages do with a
Context Pack once the LLM has answered -- deterministic post-processing
that must not depend on the model getting it right:

- the blueprint guard: a step citing a control that requires human review
  is never left fully "automatable";
- agent enrichment: escalation targets, policy guardrails, knowledge
  sources, and a "rules you must follow" prompt section from the cited
  entries;
- change impact: which packs, blueprints, agents, and regression suites a
  context change affects.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentArtifactModel,
    BlueprintOverlayModel,
    ContextEntityModel,
    ContextPackModel,
    RegressionSuiteModel,
)
from app.schemas.agents import AgentDefinition, EscalationTarget, KnowledgeSourceRef
from app.schemas.blueprint import BlueprintNodeResult
from app.schemas.context import Citation, ContextImpact

from . import repository
from .resolver import citations_for, humanize_labels, mentioned_labels, packs_containing


def _entities_by_label(session: Session, pack: ContextPackModel) -> dict[str, ContextEntityModel]:
    by_label: dict[str, ContextEntityModel] = {}
    for entry in pack.entries:
        entity = session.get(ContextEntityModel, entry["entity_id"])
        if entity is not None:
            by_label[entry["label"]] = entity
    for passage in pack.passages:
        policy_id = passage.get("policy_entity_id")
        entity = session.get(ContextEntityModel, policy_id) if policy_id else None
        if entity is not None:
            by_label[passage["label"]] = entity
    return by_label


def _requires_review(entity: ContextEntityModel) -> bool:
    return entity.kind == "control" and bool((entity.data or {}).get("requires_human_review"))


def apply_blueprint_context(
    session: Session, nodes: list[BlueprintNodeResult], pack: ContextPackModel | None
) -> list[BlueprintNodeResult]:
    """US28.3 + US28.5: turn each node's cited labels into citations, then
    enforce that a step bound by a human-review control is at most
    "partial" with a review checkpoint -- however the model scored it."""
    if pack is None:
        return nodes
    by_label = _entities_by_label(session, pack)
    for node in nodes:
        for text in (node.rationale, node.not_automatable_reason or ""):
            node.applicable_rules = list(dict.fromkeys([*node.applicable_rules, *mentioned_labels(pack, text)]))
        node.rationale = humanize_labels(pack, node.rationale)
        if node.not_automatable_reason:
            node.not_automatable_reason = humanize_labels(pack, node.not_automatable_reason)
        node.citations = citations_for(pack, node.applicable_rules)
        node.applicable_rules = [label.strip().strip("[]") for label in node.applicable_rules if label.strip().strip("[]") in by_label]
        review_controls = [by_label[label] for label in node.applicable_rules if _requires_review(by_label[label])]
        if not review_controls:
            continue
        names = ", ".join(c.code or c.name for c in review_controls)
        if node.verdict == "automatable":
            node.verdict = "partial"
            node.rationale = f"{node.rationale} (Changed from automatable to partial: {names} requires human review.)"
        if node.agent_spec is not None and node.agent_spec.human_checkpoint in ("none", "review_after_action"):
            node.agent_spec.human_checkpoint = "review_before_action"
    return nodes


def apply_agent_context(
    session: Session,
    definition: AgentDefinition,
    nodes: list[BlueprintNodeResult],
    pack: ContextPackModel | None,
) -> AgentDefinition:
    """US28.6: replaces generic defaults with what the cited context says."""
    if pack is None:
        return definition
    by_label = _entities_by_label(session, pack)
    labels: list[str] = []
    for node in nodes:
        for label in node.applicable_rules:
            if label in by_label and label not in labels:
                labels.append(label)
    if not labels:
        return definition

    entities = [by_label[label] for label in labels]
    guardrails: list[str] = list(definition.policy_guardrails)
    review_roles: list[str] = []
    for entity in entities:
        data = entity.data or {}
        if entity.kind == "control":
            for name in data.get("required_guardrails") or []:
                if name not in guardrails:
                    guardrails.append(name)
            if data.get("requires_human_review") and data.get("review_role"):
                review_roles.append(data["review_role"])
        elif entity.kind == "approval_authority" and data.get("role_name"):
            review_roles.append(data["role_name"])
    definition.policy_guardrails = guardrails

    if review_roles:
        definition.escalation_policy.escalation_target = EscalationTarget(type="human", role=review_roles[0])
        if any(_requires_review(e) for e in entities) and "policy_requires_human_review" not in definition.escalation_policy.escalate_when:
            definition.escalation_policy.escalate_when.append("policy_requires_human_review")

    sources = {(s.id, s.version) for s in definition.knowledge_sources}
    for entity in entities:
        if entity.kind in ("policy", "control", "obligation", "regulation") and (entity.id, entity.version) not in sources:
            definition.knowledge_sources.append(
                KnowledgeSourceRef(id=entity.id, version=entity.version, name=entity.code or entity.name, kind=entity.kind)
            )
            sources.add((entity.id, entity.version))

    entry_text = {e["label"]: e["text"] for e in pack.entries}
    passage_text = {p["label"]: f"{p['clause_ref']}: {p['text']}" for p in pack.passages}
    rules = [entry_text.get(label) or passage_text.get(label) for label in labels]
    definition.system_prompt = "\n".join(
        [
            definition.system_prompt,
            "",
            "Organization rules you must follow for this step:",
            *[f"- {rule}" for rule in rules if rule],
            "If following a rule needs a person, stop and escalate rather than proceeding.",
        ]
    )
    return definition


def compute_impact(session: Session, entity_id: str) -> ContextImpact:
    entity = repository.get_entity_model(session, entity_id)
    pack_ids = [p.id for p in packs_containing(session, entity_id)]
    overlays = (
        list(session.scalars(select(BlueprintOverlayModel).where(BlueprintOverlayModel.context_pack_id.in_(pack_ids))))
        if pack_ids
        else []
    )
    artifacts = (
        list(session.scalars(select(AgentArtifactModel).where(AgentArtifactModel.context_pack_id.in_(pack_ids))))
        if pack_ids
        else []
    )
    artifact_ids = [a.id for a in artifacts]
    suites = (
        list(session.scalars(select(RegressionSuiteModel).where(RegressionSuiteModel.agent_artifact_id.in_(artifact_ids))))
        if artifact_ids
        else []
    )
    label = f"{entity.code} v{entity.version}" if entity.code else f"{entity.name} v{entity.version}"
    parts = [
        f"{len(overlays)} blueprint{'s' if len(overlays) != 1 else ''}",
        f"{len(artifacts)} agent{'s' if len(artifacts) != 1 else ''}",
        f"{len(suites)} regression suite{'s' if len(suites) != 1 else ''}",
    ]
    return ContextImpact(
        entity_id=entity.id,
        entity_name=entity.name,
        pack_ids=pack_ids,
        blueprint_process_ids=[o.process_id for o in overlays],
        agent_artifact_ids=artifact_ids,
        regression_suite_ids=[s.id for s in suites],
        summary=f"{label} -- {', '.join(parts)} affected",
    )


def citations_from_labels(pack: ContextPackModel | None, labels: list[str]) -> list[Citation]:
    return citations_for(pack, labels)


def start_affected_suite_runs(
    session: Session, entity_ids: list[str], *, run_by: str | None
) -> list[tuple[str, str]]:
    """US28.8: start one run of every regression suite affected by a change
    to any of `entity_ids`. Returns (run_id, kind) pairs for the caller to
    enqueue, the same create-then-enqueue handoff as app/api/evaluation.py.
    A suite whose agent can't run right now (e.g. not SIMULATION_READY) is
    skipped rather than failing the context edit that triggered it."""
    from app.db import repository as db_repository  # local: app.db.repository imports this module
    from app.twin.errors import TwinServiceError

    suite_ids: list[str] = []
    for entity_id in entity_ids:
        for suite_id in compute_impact(session, entity_id).regression_suite_ids:
            if suite_id not in suite_ids:
                suite_ids.append(suite_id)
    queued: list[tuple[str, str]] = []
    for suite_id in suite_ids:
        suite = session.get(RegressionSuiteModel, suite_id)
        if suite is None:
            continue
        try:
            _, run_ids, kind = db_repository.start_regression_suite_run(session, suite.process_id, suite_id, run_by=run_by)
        except TwinServiceError:
            continue
        queued.extend((run_id, kind) for run_id in run_ids)
    return queued
