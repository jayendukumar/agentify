"""Epic 33: rule proposals (US33.1), oracle labelling and conflicts
(US33.2/US33.3), label-first generation (US33.4), metamorphic variants
(US33.5), judge calibration (US33.6), and the disagreement-driven review
queue (US33.7)."""

from __future__ import annotations

import json
import math
import random
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bpmn.nodes import extract_flow_nodes
from app.datasets import service as datasets
from app.db import repository
from app.db.models import (
    AgentArtifactModel,
    DecisionRuleModel,
    JudgeRubricModel,
    LabelledDatasetModel,
    MetamorphicRelationModel,
    SimulationRunModel,
)
from app.ids import new_id, utcnow
from app.llm import ChatMessage, LLMClient
from app.schemas.agents import AgentDefinition
from app.schemas.datasets import DatasetRowInput, RowLabels
from app.store import NotFoundError

from .rules import RuleSyntaxError, RuleView, render, run_oracle, validate_expression

_GATEWAY_TYPES = {"exclusiveGateway", "inclusiveGateway"}


class OracleError(Exception):
    pass


# ---- rules (US33.1) -------------------------------------------------------


def to_view(rule: DecisionRuleModel) -> RuleView:
    return RuleView(
        id=rule.id,
        gateway_id=rule.gateway_id,
        gateway_label=rule.gateway_label,
        branch_target_id=rule.branch_target_id,
        branch_label=rule.branch_label,
        condition_text=rule.condition_text,
        expression=rule.expression,
        rendered=render(rule.expression) if rule.expression else "",
        outcome=rule.outcome,
        priority=rule.priority,
        is_default=rule.is_default,
        status=rule.status,
        note=rule.note,
        control_id=rule.control_id,
        version=rule.version,
    )


def list_rules(session: Session, process_id: str) -> list[DecisionRuleModel]:
    return list(
        session.scalars(
            select(DecisionRuleModel)
            .where(DecisionRuleModel.process_id == process_id)
            .order_by(DecisionRuleModel.gateway_id, DecisionRuleModel.is_default, DecisionRuleModel.priority)
        )
    )


def process_fields(session: Session, process_id: str) -> list[str]:
    """Field names a rule may test: every agent's declared inputs, plus
    every input key seen in the process's datasets."""
    names: list[str] = []
    for artifact in session.scalars(select(AgentArtifactModel).where(AgentArtifactModel.process_id == process_id)):
        for f in (artifact.definition or {}).get("input_schema", []):
            if f.get("name") and f["name"] not in names:
                names.append(f["name"])
    for dataset in session.scalars(select(LabelledDatasetModel).where(LabelledDatasetModel.process_id == process_id)):
        for row in datasets.rows_at(session, dataset.id)[:200]:
            for key in row.inputs or {}:
                if key not in names:
                    names.append(key)
    return names


class _ProposedRule(BaseModel):
    branch_target_id: str
    expression: dict[str, Any] | None = None
    outcome: str | None = None
    is_default: bool = False
    note: str | None = None


class _ProposedRules(BaseModel):
    rules: list[_ProposedRule] = []


def _gateways(session: Session, process_id: str):
    version = repository.get_latest_version(session, process_id)
    if version is None:
        raise OracleError("Finalize the process first -- rules are drafted from the finalized diagram's decision points")
    nodes = extract_flow_nodes(version.xml)
    return [n for n in nodes if n.bpmn_type in _GATEWAY_TYPES and len(n.successors) > 1]


def _prompt(gateway, fields: list[str]) -> str:
    branches = "\n".join(
        f'- branch_target_id "{s.node_id}" -> "{s.label}"; condition: {json.dumps(s.condition or "")}' for s in gateway.successors
    )
    return f"""Turn a business process decision point into executable rules.

Decision point: "{gateway.label}"
Branches:
{branches}

Input fields a rule may test (use these exact names, nothing else): {', '.join(fields) or '(none known)'}

Rule language -- a JSON expression:
  {{"all": [cond, ...]}}, {{"any": [cond, ...]}}, {{"not": cond}},
  cond = {{"field": "<field name>", "op": "<op>", "value": <value>}}
  ops: eq ne gt gte lt lte in not_in contains missing present ("in"/"not_in" take a list; missing/present take no value)

For EACH branch return: "branch_target_id", "expression" (null if the condition cannot be expressed with these fields),
"outcome" ("approve", "reject", "escalate", or null if the branch does not decide the case), "is_default" (true for an
"otherwise"/no-condition branch), and "note" (why, when expression is null).

Respond with JSON: {{"rules": [ ... ]}}"""


async def propose_rules(session: Session, llm: LLMClient, process_id: str) -> list[DecisionRuleModel]:
    """Drafts rules for every decision point that has none yet. Drafts are
    validated against the rule language and field list -- anything that
    fails is kept as "unstructurable" with the original text, never as a
    rule that could execute wrongly."""
    fields = process_fields(session, process_id)
    existing = {(r.gateway_id, r.branch_target_id) for r in list_rules(session, process_id)}
    created: list[DecisionRuleModel] = []
    for gateway in _gateways(session, process_id):
        pending = [s for s in gateway.successors if (gateway.id, s.node_id) not in existing]
        if not pending:
            continue
        reply = await llm.complete(
            [ChatMessage(role="system", content=_prompt(gateway, fields))],
            operation="oracle_rule_structuring",
            response_format={"type": "json_object"},
            max_tokens=8000,
        )
        try:
            proposed = _ProposedRules.model_validate(json.loads(reply.text or "{}")).rules
        except (json.JSONDecodeError, ValidationError) as exc:
            raise OracleError(f"The AI returned rules in an unexpected shape: {exc}") from exc
        by_target = {p.branch_target_id: p for p in proposed}
        for priority, successor in enumerate(pending):
            draft = by_target.get(successor.node_id)
            expression, status, note = None, "unstructurable", "No rule was proposed for this branch"
            outcome = draft.outcome if draft and draft.outcome in ("approve", "reject", "escalate") else None
            if draft is not None:
                note = draft.note
                if draft.is_default:
                    status = "proposed"
                elif draft.expression is not None:
                    try:
                        validate_expression(draft.expression, set(fields) if fields else None)
                        expression, status = draft.expression, "proposed"
                    except RuleSyntaxError as exc:
                        note = f"Draft rejected: {exc}"
            rule = DecisionRuleModel(
                id=new_id("rule"),
                process_id=process_id,
                gateway_id=gateway.id,
                gateway_label=gateway.label,
                branch_target_id=successor.node_id,
                branch_label=successor.condition or successor.label,
                condition_text=successor.condition or "",
                expression=expression,
                outcome=outcome,
                priority=(priority + 1) * 10,
                is_default=bool(draft and draft.is_default),
                status=status,
                note=note,
            )
            session.add(rule)
            created.append(rule)
    session.flush()
    return created


class RuleUpdate(BaseModel):
    expression: dict[str, Any] | None = None
    outcome: str | None = None
    priority: int | None = None
    is_default: bool | None = None
    control_id: str | None = None
    confirm: bool = False


def update_rule(session: Session, process_id: str, rule_id: str, data: RuleUpdate, *, user_id: str | None) -> DecisionRuleModel:
    rule = session.get(DecisionRuleModel, rule_id)
    if rule is None or rule.process_id != process_id:
        raise NotFoundError("rule", rule_id)
    if data.expression is not None:
        try:
            validate_expression(data.expression, set(process_fields(session, process_id)) or None)
        except RuleSyntaxError as exc:
            raise OracleError(str(exc)) from exc
        rule.expression = data.expression
        rule.version += 1
        rule.status = "proposed"
    if data.outcome is not None:
        if data.outcome not in ("approve", "reject", "escalate", ""):
            raise OracleError("Outcome must be approve, reject, escalate, or empty")
        rule.outcome = data.outcome or None
    if data.priority is not None:
        rule.priority = data.priority
    if data.is_default is not None:
        rule.is_default = data.is_default
    if data.control_id is not None:
        rule.control_id = data.control_id or None
    if data.confirm:
        if rule.expression is None and not rule.is_default:
            raise OracleError("A rule needs an expression (or to be the default branch) before it can be confirmed")
        rule.status = "confirmed"
        rule.confirmed_by = user_id
        rule.confirmed_at = utcnow().replace(tzinfo=None)
    session.flush()
    return rule


def confirmed_views(session: Session, process_id: str) -> list[RuleView]:
    return [to_view(r) for r in list_rules(session, process_id) if r.status == "confirmed"]


# ---- oracle labelling and conflicts (US33.2/US33.3) -----------------------


class OracleLabelResult(BaseModel):
    version: int
    labelled: int = 0
    agreed: int = 0
    conflicts: int = 0
    undetermined: int = 0


class OracleConflict(BaseModel):
    row_key: str
    inputs: dict[str, Any]
    label: str | None
    label_provenance: str
    oracle: str
    fired_rules: list[str] = []


def label_with_oracle(session: Session, dataset: LabelledDatasetModel, *, user_id: str | None) -> OracleLabelResult:
    """Fills in the decision of every unlabelled row the rules determine,
    as an oracle-provenance label. Rows that already have a label are
    compared, never overwritten: disagreement is a conflict for a person."""
    rules = confirmed_views(session, dataset.process_id)
    if not rules:
        raise OracleError("No confirmed rules yet -- confirm the decision rules first")
    result = OracleLabelResult(version=dataset.version)
    to_label = []
    for row in datasets.rows_at(session, dataset.id):
        verdict = run_oracle(rules, row.inputs or {})
        if verdict.decision is None:
            result.undetermined += 1
            continue
        current = (row.labels or {}).get("decision")
        if current is None:
            to_label.append((row, verdict.decision))
        elif current == verdict.decision:
            result.agreed += 1
        else:
            result.conflicts += 1
    if to_label:
        version = datasets._bump(dataset)
        for row, decision in to_label:
            labels = {**(row.labels or {}), "decision": decision}
            session.add(datasets._new_revision(dataset, version, base=row, labels=labels, provenance="oracle"))
        session.flush()
        result.labelled = len(to_label)
        result.version = version
    return result


def oracle_conflicts(session: Session, dataset: LabelledDatasetModel) -> list[OracleConflict]:
    rules = confirmed_views(session, dataset.process_id)
    conflicts = []
    for row in datasets.rows_at(session, dataset.id):
        label = (row.labels or {}).get("decision")
        if label is None or row.provenance == "oracle":
            continue
        verdict = run_oracle(rules, row.inputs or {})
        if verdict.decision is not None and verdict.decision != label:
            conflicts.append(
                OracleConflict(
                    row_key=row.row_key,
                    inputs=row.inputs or {},
                    label=label,
                    label_provenance=row.provenance,
                    oracle=verdict.decision,
                    fired_rules=verdict.fired,
                )
            )
    return conflicts


def report_rules_gap(session: Session, dataset: LabelledDatasetModel, conflict_keys: list[str]) -> str:
    """US33.3: the rules as documented don't match how the process really
    runs -- raised as a gap finding on the process (Epic 11) so the
    documentation gets fixed, not just the test data."""
    from app.schemas.gap_analysis import GapFindingOption

    conflicts = [c for c in oracle_conflicts(session, dataset) if c.row_key in conflict_keys]
    if not conflicts:
        raise OracleError("None of those rows conflict with the rules")
    rules = {r.id: r for r in list_rules(session, dataset.process_id)}
    fired = sorted({rules[i].gateway_label or rules[i].gateway_id for c in conflicts for i in c.fired_rules if i in rules})
    example = conflicts[0]
    finding = repository.add_gap_finding(
        session,
        dataset.process_id,
        kind="structural",
        question=(
            f"{len(conflicts)} real case(s) in '{dataset.name}' were decided differently from what the documented rules at "
            f"{', '.join(fired) or 'the decision points'} say (e.g. one was '{example.label}' where the rules give "
            f"'{example.oracle}'). Is the documented rule out of date?"
        ),
        target_element_ids=[],
        options=[GapFindingOption(label="Update the decision point's condition to match practice", diff=None)],
    )
    return finding.id


# ---- label-first generation (US33.4) --------------------------------------


class GenerateRequest(BaseModel):
    total: int = Field(default=50, ge=1, le=2000)
    mix: dict[str, float] = {"approve": 0.7, "reject": 0.2, "escalate": 0.1}
    messy_fraction: float = Field(default=0.0, ge=0, le=1)
    segment: str = "generated"


class GenerateResult(BaseModel):
    requested: int
    kept: int
    discarded: int
    by_decision: dict[str, int] = {}
    version: int


def _targets(total: int, mix: dict[str, float]) -> dict[str, int]:
    weights = {k: v for k, v in mix.items() if k in ("approve", "reject", "escalate") and v > 0}
    if not weights:
        raise OracleError("The mix needs at least one of approve, reject, escalate")
    scale = sum(weights.values())
    counts = {k: math.floor(total * v / scale) for k, v in weights.items()}
    for key in sorted(weights, key=lambda k: -weights[k])[: total - sum(counts.values())]:
        counts[key] += 1
    return counts


async def generate_label_first(
    session: Session, llm: LLMClient, dataset: LabelledDatasetModel, request: GenerateRequest, *, user_id: str | None
) -> GenerateResult:
    """Choose the answer, generate inputs that should produce it, keep only
    what the rule oracle confirms. Generation runs in batches of at most
    25 records (Epic 25's per-call cap applies per call, not per dataset)."""
    from app.twin import test_builder

    rules = confirmed_views(session, dataset.process_id)
    if not rules:
        raise OracleError("Confirm the decision rules first -- they're what makes generated labels trustworthy")
    artifact = session.get(AgentArtifactModel, dataset.agent_artifact_id) if dataset.agent_artifact_id else None
    if artifact is None:
        raise OracleError("Link the dataset to an agent first -- generation needs its input fields")
    definition = AgentDefinition.model_validate(artifact.definition)
    context = test_builder.build_context(artifact_id=artifact.id, definition=definition, node_ids=list(artifact.node_ids), baseline_xml=None)
    rule_text = "\n".join(
        f"- at '{r.gateway_label}': if {r.rendered or 'otherwise'} then branch '{r.branch_label}'"
        + (f" (case is {r.outcome})" if r.outcome else "")
        for r in rules
    )
    rows: list[DatasetRowInput] = []
    discarded = 0
    by_decision: dict[str, int] = {}
    for decision, count in _targets(request.total, request.mix).items():
        remaining = count
        attempts = 0
        while remaining > 0 and attempts < max(3, count // 10 + 3):
            attempts += 1
            batch = min(25, remaining)
            messy = request.messy_fraction > 0 and random.random() < request.messy_fraction
            prompt = (
                f"Every record must be a case these rules decide as '{decision}':\n{rule_text}\n"
                + ("Make the formatting messy (typos, odd casing, extra spaces) without changing what the rules decide.\n" if messy else "")
                + "Vary the records realistically; stay clearly inside the rule (not on a boundary)."
            )
            records = await test_builder.generate_records(llm, context, mode="describe", count=batch, prompt=prompt)
            for record in records:
                verdict = run_oracle(rules, record.record)
                if verdict.decision != decision:
                    discarded += 1
                    continue
                rows.append(
                    DatasetRowInput(
                        inputs=record.record,
                        labels=RowLabels(decision=decision),
                        segment=f"{request.segment}-messy" if messy else request.segment,
                        provenance="synthetic",
                        tags=[f"oracle:{decision}"],
                    )
                )
                by_decision[decision] = by_decision.get(decision, 0) + 1
                remaining -= 1
                if remaining == 0:
                    break
    version = datasets.add_rows(session, dataset, rows, created_by=user_id) if rows else dataset.version
    return GenerateResult(requested=request.total, kept=len(rows), discarded=discarded, by_decision=by_decision, version=version)


# ---- metamorphic relations (US33.5) ---------------------------------------


class RelationCreate(BaseModel):
    name: str
    kind: str = Field(pattern="^(invariance|directional)$")
    field: str
    perturbation: dict[str, Any]
    direction: str | None = Field(default=None, pattern="^(toward_approve|toward_reject|toward_escalate)$")


_PERTURBATIONS = {"uppercase", "lowercase", "whitespace", "titlecase", "set", "scale", "add", "remove"}


def perturb(value: Any, perturbation: dict[str, Any]) -> Any:
    kind = perturbation.get("type")
    if kind == "uppercase":
        return str(value).upper()
    if kind == "lowercase":
        return str(value).lower()
    if kind == "titlecase":
        return str(value).title()
    if kind == "whitespace":
        return f"  {value}  "
    if kind == "set":
        return perturbation.get("value")
    if kind == "remove":
        return None
    from .rules import _as_number

    number = _as_number(value)
    if number is None:
        return value
    if kind == "scale":
        result = number * float(perturbation.get("factor", 1))
    elif kind == "add":
        result = number + float(perturbation.get("amount", 0))
    else:
        return value
    return int(result) if float(result).is_integer() else round(result, 2)


def create_relation(session: Session, dataset: LabelledDatasetModel, data: RelationCreate) -> MetamorphicRelationModel:
    if data.perturbation.get("type") not in _PERTURBATIONS:
        raise OracleError(f"Perturbation type must be one of: {', '.join(sorted(_PERTURBATIONS))}")
    if data.kind == "directional" and data.direction is None:
        raise OracleError("A directional relation needs a direction")
    relation = MetamorphicRelationModel(
        id=new_id("rel"),
        dataset_id=dataset.id,
        name=data.name,
        kind=data.kind,
        field=data.field,
        perturbation=data.perturbation,
        direction=data.direction,
    )
    session.add(relation)
    session.flush()
    return relation


def generate_variants(
    session: Session, dataset: LabelledDatasetModel, relation: MetamorphicRelationModel, *, limit: int = 200, user_id: str | None
) -> int:
    """Deterministic perturbations -- no LLM. Each variant row is tagged
    with its base row and the relation, so batch results (Epic 35) can
    compare base and variant decisions."""
    base_rows = [
        r for r in datasets.rows_at(session, dataset.id)
        if relation.field in (r.inputs or {}) and not any(t.startswith("variant-of:") for t in r.tags or [])
    ][:limit]
    rows = []
    for row in base_rows:
        inputs = dict(row.inputs)
        new_value = perturb(inputs[relation.field], relation.perturbation)
        if new_value is None:
            inputs.pop(relation.field, None)
        else:
            inputs[relation.field] = new_value
        labels = dict(row.labels or {}) if relation.kind == "invariance" else {}
        rows.append(
            DatasetRowInput(
                inputs=inputs,
                labels=RowLabels.model_validate(labels),
                segment=f"variant-{relation.kind}",
                provenance="synthetic",
                tags=[f"variant-of:{row.row_key}", f"relation:{relation.id}"],
            )
        )
    if rows:
        datasets.add_rows(session, dataset, rows, created_by=user_id)
    return len(rows)


_ORDER = {"approve": 0, "escalate": 1, "reject": 2}


def relation_holds(relation: MetamorphicRelationModel, base_decision: str | None, variant_decision: str | None) -> bool | None:
    """None when either decision is unknown."""
    if base_decision is None or variant_decision is None:
        return None
    if relation.kind == "invariance":
        return base_decision == variant_decision
    # Directional: the variant lands on the target outcome, or strictly
    # closer to it on approve < escalate < reject.
    target = (relation.direction or "").removeprefix("toward_")
    if variant_decision == target:
        return True
    distance = lambda d: abs(_ORDER[d] - _ORDER[target])  # noqa: E731
    return distance(variant_decision) < distance(base_decision)


# ---- calibrated judge (US33.6) --------------------------------------------


class RubricCreate(BaseModel):
    name: str
    field: str
    rubric: str = Field(min_length=10)
    judge_model: str | None = None
    min_agreement: float = Field(default=0.85, gt=0, le=1)
    min_samples: int = Field(default=20, ge=5)


class JudgeVerdict(BaseModel):
    passed: bool
    reason: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


async def judge(llm: LLMClient, rubric: JudgeRubricModel, output_text: str, inputs: dict[str, Any]) -> JudgeVerdict:
    reply = await llm.complete(
        [
            ChatMessage(
                role="system",
                content=(
                    "You grade one field of an AI agent's output against a rubric, strictly and consistently.\n\n"
                    f"Rubric:\n{rubric.rubric}\n\nCase inputs: {json.dumps(inputs, default=str)[:4000]}\n\n"
                    f'Output field "{rubric.field}":\n"""{output_text[:6000]}"""\n\n'
                    'Respond with JSON: {"passed": true|false, "reason": "<one sentence>", "confidence": 0-1}'
                ),
            )
        ],
        operation="oracle_judge",
        response_format={"type": "json_object"},
        model=rubric.judge_model,
        max_tokens=4000,
    )
    try:
        return JudgeVerdict.model_validate(json.loads(reply.text or "{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise OracleError(f"The judge returned an unexpected shape: {exc}") from exc


def _kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(a == b for a, b in pairs) / n
    p_human = sum(a for a, _ in pairs) / n
    p_judge = sum(b for _, b in pairs) / n
    expected = p_human * p_judge + (1 - p_human) * (1 - p_judge)
    return 1.0 if expected == 1 else round((observed - expected) / (1 - expected), 3)


async def calibrate(session: Session, llm: LLMClient, rubric: JudgeRubricModel, dataset: LabelledDatasetModel) -> dict[str, Any]:
    """Runs the judge on every row that has a human verdict for this rubric
    (`labels.outputs["judgment:<rubric id>"]`) and the latest run's output
    for the field, then records agreement and Cohen's kappa. Gate-eligible
    only at or above `min_agreement` over at least `min_samples`."""
    key = f"judgment:{rubric.id}"
    pairs: list[tuple[bool, bool]] = []
    disagreements = []
    for row in datasets.rows_at(session, dataset.id):
        human = (row.labels or {}).get("outputs", {}).get(key)
        if not isinstance(human, bool):
            continue
        run = session.scalar(
            select(SimulationRunModel)
            .where(SimulationRunModel.dataset_id == dataset.id, SimulationRunModel.row_key == row.row_key)
            .order_by(SimulationRunModel.started_at.desc())
            .limit(1)
        )
        output = (run.final_output or {}).get(rubric.field) if run is not None else None
        if not output:
            continue
        verdict = await judge(llm, rubric, str(output), row.inputs or {})
        pairs.append((human, verdict.passed))
        if human != verdict.passed:
            disagreements.append({"row_key": row.row_key, "human": human, "judge": verdict.passed, "reason": verdict.reason})
    agreement = round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None
    calibration = {
        "rubric_version": rubric.version,
        "judge_model": rubric.judge_model,
        "samples": len(pairs),
        "agreement": agreement,
        "kappa": _kappa(pairs),
        "gate_eligible": bool(agreement is not None and len(pairs) >= rubric.min_samples and agreement >= rubric.min_agreement),
        "disagreements": disagreements[:20],
        "calibrated_at": utcnow().isoformat(),
    }
    rubric.calibration = calibration
    session.flush()
    return calibration


def gate_eligible(rubric: JudgeRubricModel) -> bool:
    """A rubric or model change since calibration voids it."""
    cal = rubric.calibration or {}
    return bool(cal.get("gate_eligible")) and cal.get("rubric_version") == rubric.version and cal.get("judge_model") == rubric.judge_model


# ---- disagreement-driven review queue (US33.7) ----------------------------


class ReviewItem(BaseModel):
    row_key: str
    inputs: dict[str, Any]
    label: str | None
    oracle: str | None
    agent: str | None
    critical: bool
    disagreements: list[str]
    score: int


def review_queue(session: Session, dataset: LabelledDatasetModel, *, limit: int = 100) -> list[ReviewItem]:
    from app.datasets.runtime import decision_from_output

    rules = confirmed_views(session, dataset.process_id)
    latest: dict[str, SimulationRunModel] = {}
    for run in session.scalars(
        select(SimulationRunModel).where(SimulationRunModel.dataset_id == dataset.id).order_by(SimulationRunModel.started_at.desc())
    ):
        if run.row_key:
            latest.setdefault(run.row_key, run)
    items = []
    for row in datasets.rows_at(session, dataset.id):
        label = (row.labels or {}).get("decision")
        oracle = run_oracle(rules, row.inputs or {}).decision if rules else None
        run = latest.get(row.row_key)
        agent = None
        if run is not None:
            agent = "escalate" if run.status == "ESCALATED" else decision_from_output(run.final_output, "decision")
        reasons = []
        if label and oracle and label != oracle:
            reasons.append(f"label says {label}, rules say {oracle}")
        if label and agent and label != agent:
            reasons.append(f"label says {label}, agent decided {agent}")
        if not label and oracle and agent and oracle != agent:
            reasons.append(f"rules say {oracle}, agent decided {agent}")
        if row.ambiguous:
            reasons.append("marked ambiguous")
        if row.second_labels and (row.second_labels or {}).get("decision") != label:
            reasons.append("reviewers disagree")
        if not reasons:
            continue
        items.append(
            ReviewItem(
                row_key=row.row_key,
                inputs=row.inputs or {},
                label=label,
                oracle=oracle,
                agent=agent,
                critical=row.critical,
                disagreements=reasons,
                score=(10 if row.critical else 0) + len(reasons),
            )
        )
    items.sort(key=lambda i: -i.score)
    return items[:limit]
