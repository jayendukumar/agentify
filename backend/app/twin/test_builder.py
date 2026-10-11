"""Epics 24/25: the guided Digital Twin test builder's service layer.

`build_context` is deterministic -- it reads the agent artifact and the
baseline BPMN it was generated from (gateway branches next to the agent's
steps) and derives the coverage targets a test suite should hit. The
LLM-backed functions (`suggest_situations`, `describe_situation`,
`generate_records`, `generate_personas`) each make one structured-JSON
completion, mirroring app/twin/schema_inference.py: parsed into Pydantic,
everything the model echoes back reconciled against the known-valid
system/field/target names (never trusted as-is), malformed output raised
as TwinServiceError for the API layer to turn into a 502.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.bpmn.nodes import FlowNodeInfo, extract_flow_nodes
from app.llm import ChatMessage, LLMClient
from app.schemas.agents import AgentDefinition
from app.schemas.twin import ComplianceAssertion, CoverageTarget, ReviewerSetup, ScenarioStory, SituationExpectation
from app.schemas.twin_builder import (
    AuthorityInfo,
    DataMode,
    GatewayBranch,
    GatewayContext,
    GeneratedPersonas,
    GeneratedRecord,
    GeneratedRecords,
    ReviewerPersona,
    RuleTarget,
    Situation,
    TestBuilderContext,
)

from .errors import TwinServiceError


class TestBuilderInputError(TwinServiceError):
    """The request was missing something the chosen mode needs -- the
    caller's mistake (400), unlike a malformed LLM response (502)."""

    __test__ = False  # "Test..." name -- not a pytest test class


# A threshold reviewer compares one numeric field of the agent's approval
# request, and the engine raises if that field is absent (app/twin/
# simulators.py's resolve_human_decision) -- so the builder only offers the
# agent's own numeric *input* fields, the ones it's likeliest to pass
# along. Found live: an unconstrained model picked an output field.
_NUMERIC_FORMAT = re.compile(r"number|numeric|amount|int|decimal|float|currency|money|score|count|quantity|price", re.I)


def numeric_input_fields(context: TestBuilderContext) -> list[str]:
    return [f.name for f in context.input_fields if _NUMERIC_FORMAT.search(f.format)]


def _usable_reviewer(raw: dict[str, Any], context: TestBuilderContext) -> ReviewerSetup:
    behaviour = raw.get("behaviour")
    if behaviour == "threshold":
        field = raw.get("field")
        threshold = raw.get("threshold")
        if field in numeric_input_fields(context) and isinstance(threshold, (int, float)):
            return ReviewerSetup(behaviour="threshold", field=field, threshold=float(threshold))
        # A strict reviewer whose limit can't be checked is closest to "rejects".
        return ReviewerSetup(behaviour="reject")
    if behaviour in ("approve", "reject"):
        return ReviewerSetup(behaviour=behaviour)
    return ReviewerSetup(behaviour="approve")


_GATEWAY_TYPES = {"exclusiveGateway", "inclusiveGateway", "parallelGateway"}
_T = TypeVar("_T", bound=BaseModel)


def build_context(
    *,
    artifact_id: str,
    definition: AgentDefinition,
    node_ids: list[str],
    baseline_xml: str | None,
    rules: list[RuleTarget] | None = None,
    authorities: list[AuthorityInfo] | None = None,
    jurisdictions: list[str] | None = None,
    data_classifications: list[str] | None = None,
) -> TestBuilderContext:
    flow_nodes = extract_flow_nodes(baseline_xml) if baseline_xml else []
    by_id: dict[str, FlowNodeInfo] = {node.id: node for node in flow_nodes}
    own_ids = set(node_ids)

    # Gateways the agent's own steps lead into (its decisions get routed
    # there) plus any gateway that's itself part of the agent's group.
    gateway_ids: list[str] = []
    for node_id in node_ids:
        node = by_id.get(node_id)
        if node is None:
            continue
        candidates = [node_id] if node.bpmn_type in _GATEWAY_TYPES else []
        candidates += [s.node_id for s in node.successors if by_id[s.node_id].bpmn_type in _GATEWAY_TYPES]
        for candidate in candidates:
            if candidate not in gateway_ids:
                gateway_ids.append(candidate)

    gateways = [
        GatewayContext(
            id=gid,
            label=by_id[gid].label,
            branches=[
                GatewayBranch(target_id=s.node_id, target_label=s.label, condition=s.condition)
                for s in by_id[gid].successors
            ],
        )
        for gid in gateway_ids
        if len(by_id[gid].successors) > 1
    ]

    targets: list[CoverageTarget] = [
        CoverageTarget(id="happy_path", kind="happy_path", label="A typical case goes through smoothly")
    ]
    for gateway in gateways:
        for branch in gateway.branches:
            targets.append(
                CoverageTarget(
                    id=f"gw:{gateway.id}:{branch.target_id}",
                    kind="gateway_branch",
                    label=f"{gateway.label} -> {branch.condition or branch.target_label}",
                )
            )
    if definition.human_checkpoint != "none":
        targets.append(CoverageTarget(id="checkpoint:approve", kind="checkpoint", label="Reviewer approves"))
        targets.append(CoverageTarget(id="checkpoint:reject", kind="checkpoint", label="Reviewer rejects"))
    for system in definition.tools_systems_needed:
        targets.append(
            CoverageTarget(id=f"sys:{system}", kind="system_failure", label=f"{system} is unavailable or misbehaves")
        )
    if definition.input_schema:
        targets.append(
            CoverageTarget(id="data_quality", kind="data_quality", label="Incomplete or messy input data")
        )
    for rule in rules or []:
        targets.append(CoverageTarget(id=rule.id, kind="control", label=f"Rule {rule.code or rule.name}: {rule.name}"))

    return TestBuilderContext(
        artifact_id=artifact_id,
        agent_name=definition.name,
        purpose=definition.purpose,
        trigger=definition.trigger,
        step_labels=[by_id[n].label for n in node_ids if n in by_id and n in own_ids],
        input_fields=definition.input_schema,
        output_fields=definition.output_schema,
        systems=definition.tools_systems_needed,
        human_checkpoint=definition.human_checkpoint,
        success_criteria=definition.success_criteria,
        completion_conditions=definition.completion_conditions,
        gateways=gateways,
        coverage_targets=targets,
        rules=rules or [],
        approval_authorities=authorities or [],
        jurisdictions=jurisdictions or [],
        data_classifications=data_classifications or [],
    )


# ---------------------------------------------------------------- LLM helpers


async def _complete_json(llm: LLMClient, prompt: str, *, operation: str, model: type[_T]) -> _T:
    result = await llm.complete(
        [ChatMessage(role="system", content=prompt)],
        operation=operation,
        response_format={"type": "json_object"},
        max_tokens=12000,
    )
    if not result.text:
        raise TwinServiceError("The AI returned no content -- please try again")
    try:
        return model.model_validate(json.loads(result.text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise TwinServiceError(f"The AI returned output in an unexpected shape -- please try again ({exc})") from exc


def _fields_block(fields: list[Any]) -> str:
    if not fields:
        return "  (none declared)"
    return "\n".join(f"  - {f.name} ({f.format}) -- {f.source_or_destination}" for f in fields)


def _rules_block(context: TestBuilderContext) -> str:
    """Epic 30, US30.2/US30.3: the organization's rules and approval limits."""
    if not context.rules and not context.approval_authorities:
        return ""
    lines = []
    if context.rules:
        lines.append("Organization rules this agent must obey (coverage target id first):")
        for rule in context.rules:
            demand = f" Requires human review{' by ' + rule.review_role if rule.review_role else ''}." if rule.requires_human_review else ""
            lines.append(f"  - {rule.id}: {rule.code or rule.name} -- {rule.description}{demand}")
    if context.approval_authorities:
        lines.append("Approval limits (who may approve what):")
        for a in context.approval_authorities:
            limit = "no limit" if a.limit_amount is None else f"up to {a.limit_amount:g} {a.currency}".strip()
            lines.append(f"  - {a.role}: {a.decision_type}, {limit}")
    return "\n".join(lines) + "\n"


def _context_block(context: TestBuilderContext) -> str:
    gateways = (
        "\n".join(
            f"  - [{g.id}] {g.label}: "
            + "; ".join(f"branch '{b.condition or b.target_label}' leads to '{b.target_label}'" for b in g.branches)
            for g in context.gateways
        )
        or "  (none)"
    )
    targets = "\n".join(f"  - {t.id}: {t.label}" for t in context.coverage_targets)
    systems = "\n".join(f"  - {s}" for s in context.systems) or "  (none)"
    numeric = numeric_input_fields(context)
    threshold_note = (
        f"A 'threshold' reviewer may only check one of these numeric input fields: {', '.join(numeric)}"
        if numeric
        else "This agent has no numeric input fields, so never use a 'threshold' reviewer"
    )
    return (
        f"Agent: {context.agent_name}\n"
        f"Purpose: {context.purpose}\n"
        f"Trigger: {context.trigger}\n"
        f"Process steps it covers: {', '.join(context.step_labels) or '(unknown)'}\n"
        f"Input fields:\n{_fields_block(context.input_fields)}\n"
        f"Output fields:\n{_fields_block(context.output_fields)}\n"
        f"Systems it uses (exact names):\n{systems}\n"
        f"Human review checkpoint: {context.human_checkpoint}\n"
        f"Success criteria: {context.success_criteria or '(not stated)'}\n"
        f"Decision points right after this agent:\n{gateways}\n"
        f"Coverage targets (exact ids):\n{targets}\n"
        f"{_rules_block(context)}"
        f"{threshold_note}.\n"
        f"Today's date is {date.today().isoformat()} -- use realistic, current dates.\n"
    )


_SITUATION_SHAPE = (
    '{"title": str (short, vivid, business language), '
    '"given": str (the case and its data, one sentence), '
    '"when": str (what happens around it -- system conditions, timing; one sentence), '
    '"then": str (what a correct agent should do, one sentence), '
    '"covers": [coverage target ids from the list above], '
    '"inputs": {<input field name>: <realistic value>, ...}, '
    '"system_behaviour": {<exact system name>: one of "normal"|"down"|"timeout"|"denied"|"bad_data"|"no_record"|"custom_data"}, '
    '"system_data": {<exact system name>: {<field>: <value>}} (only for systems set to "custom_data" -- the record that system returns), '
    '"reviewer": {"behaviour": one of "none"|"approve"|"reject"|"threshold", "field": str|null, "threshold": number|null}, '
    '"expect": {"systems_used": [exact system names the agent should call, in order], '
    '"review_decision": "approve"|"reject"|null, "output_fields": [output field names that must be produced], '
    '"compliance": [{"control": <a rule coverage target id, e.g. "ctl:...">, '
    '"kind": "escalated"|"blocked"|"reviewed"|"logged", "role": str|null, "system": <exact system name>|null, '
    '"field": <output field name>|null}] (only for situations testing a rule -- what the rule demands)}}'
)


def _sanitize_situation(raw: dict[str, Any], context: TestBuilderContext, *, source: str) -> Situation | None:
    if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
        return None
    systems = set(context.systems)
    target_ids = {t.id for t in context.coverage_targets}
    output_names = {f.name for f in context.output_fields}
    input_names = {f.name for f in context.input_fields}
    has_checkpoint = context.human_checkpoint != "none"

    behaviour_raw = raw.get("system_behaviour") or {}
    behaviour = {
        name: value
        for name, value in behaviour_raw.items()
        if name in systems and value in ("normal", "down", "timeout", "denied", "bad_data", "no_record", "custom_data")
    }
    system_data = {
        name: value
        for name, value in (raw.get("system_data") or {}).items()
        if name in systems and isinstance(value, dict) and behaviour.get(name) == "custom_data"
    }
    for name, value in list(behaviour.items()):
        if value == "custom_data" and name not in system_data:
            behaviour[name] = "normal"

    reviewer = _usable_reviewer(raw.get("reviewer") or {}, context) if has_checkpoint else ReviewerSetup()

    expect_raw = raw.get("expect") or {}
    decision = expect_raw.get("review_decision")
    rule_ids = {r.id for r in context.rules}
    compliance = []
    for item in expect_raw.get("compliance") or []:
        if not isinstance(item, dict) or item.get("control") not in rule_ids:
            continue
        if item.get("kind") not in ("escalated", "blocked", "reviewed", "logged"):
            continue
        compliance.append(
            ComplianceAssertion(
                control_id=item["control"],
                kind=item["kind"],
                role=item.get("role") if isinstance(item.get("role"), str) and item.get("role") else None,
                system=item.get("system") if item.get("system") in systems else None,
                field=item.get("field") if item.get("field") in output_names else None,
            )
        )
    expect = SituationExpectation(
        systems_used=[s for s in (expect_raw.get("systems_used") or []) if s in systems],
        review_decision=decision if has_checkpoint and decision in ("approve", "reject") else None,
        output_fields=[f for f in (expect_raw.get("output_fields") or []) if f in output_names],
        compliance=compliance,
    )

    inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    if input_names:
        inputs = {k: v for k, v in inputs.items() if k in input_names}

    covers = [c for c in (raw.get("covers") or []) if c in target_ids]
    for assertion in compliance:  # a situation asserting a rule covers it
        if assertion.control_id not in covers:
            covers.append(assertion.control_id)
    story = ScenarioStory(
        title=str(raw["title"]).strip()[:120],
        given=str(raw.get("given") or ""),
        when=str(raw.get("when") or ""),
        then=str(raw.get("then") or ""),
        covers=covers,
        source=source,  # type: ignore[arg-type]
        system_behaviour=behaviour,
        system_data=system_data,
        reviewer=reviewer,
        expect=expect,
    )
    return Situation(story=story, inputs=inputs)


class _RawSituations(BaseModel):
    situations: list[dict[str, Any]]


async def suggest_situations(
    llm: LLMClient,
    context: TestBuilderContext,
    *,
    count: int,
    focus: str | None = None,
    already_covered: list[str] | None = None,
) -> list[Situation]:
    covered = ", ".join(already_covered or []) or "(nothing yet)"
    prompt = (
        "You help a business process owner test an AI agent in a safe simulated copy of their "
        "process (a 'digital twin'). Nothing is called live.\n\n"
        f"{_context_block(context)}\n"
        f"Already covered by existing tests: {covered}\n"
        + (f"The user especially wants to explore: {focus}\n" if focus else "")
        + f"\nPropose {count} distinct, realistic test situations, written for a non-technical business "
        "reader. Prioritise coverage targets not yet covered: start with one typical happy-path case, then "
        "decision-point branches, reviewer outcomes, a system being unavailable, and messy input data. "
        + (
            "Include at least one situation designed to trigger each organization rule listed above, citing "
            "its coverage target id in covers and stating in expect.compliance what the rule demands "
            "(escalation to the named role, a blocked action, a review before acting, or a logged decision). "
            if context.rules
            else ""
        )
        + "Use realistic, specific values (names, amounts, dates) -- invent them, never use real people. "
        "Only use the exact system names, field names, and coverage target ids given above.\n\n"
        f'Respond as JSON: {{"situations": [{_SITUATION_SHAPE}, ...]}}'
    )
    parsed = await _complete_json(llm, prompt, operation="twin_builder_suggest", model=_RawSituations)
    situations = [s for raw in parsed.situations if (s := _sanitize_situation(raw, context, source="suggested"))]
    if not situations:
        raise TwinServiceError("The AI didn't propose any usable situations -- please try again")
    return situations[:count]


async def describe_situation(llm: LLMClient, context: TestBuilderContext, description: str) -> Situation:
    prompt = (
        "You help a business process owner test an AI agent in a safe simulated copy of their process.\n\n"
        f"{_context_block(context)}\n"
        "The user described a situation they want to test, in their own words:\n"
        f'"""{description}"""\n\n'
        "Turn it into exactly one structured test situation that faithfully captures what they asked for -- "
        "fill in realistic specifics they left out. Only use the exact system names, field names, and "
        "coverage target ids given above.\n\n"
        f'Respond as JSON: {{"situations": [{_SITUATION_SHAPE}]}}'
    )
    parsed = await _complete_json(llm, prompt, operation="twin_builder_describe", model=_RawSituations)
    for raw in parsed.situations:
        situation = _sanitize_situation(raw, context, source="described")
        if situation is not None:
            return situation
    raise TwinServiceError("The AI couldn't turn that description into a test situation -- try rephrasing it")


# ------------------------------------------------------------------ Data kitchen

_MODE_INSTRUCTIONS: dict[str, str] = {
    "typical": "Generate typical, realistic, everyday records -- the kind that make up most real cases.",
    "edge": (
        "Generate valid but unusual records that probe boundaries: very large/small amounts, dates at "
        "month/year end, rare categories, maximum-length text, zero values."
    ),
    "messy": (
        "Generate records that look like real-world messy data: typos, inconsistent casing and date formats, "
        "extra whitespace, missing optional fields, abbreviations, a field holding the wrong kind of value."
    ),
    "describe": "Generate records that match the user's description below as closely as possible.",
    "mutate": (
        "Starting from the seed record below, generate labelled variants that each change ONE thing to make "
        "the case trickier: a boundary value, a missing field, a wrong format, a near-duplicate, a value in "
        "another language, contradictory fields."
    ),
    "clone": (
        "The user pasted a real example below. Produce look-alike SYNTHETIC records with the same structure, "
        "value styles, and realism -- but replace every name, email, phone number, address, account/ID number, "
        "and any other identifying detail with invented values. Never copy identifying values through."
    ),
    "near_miss": (
        "Generate records designed to test screening and threshold rules: names that are one or two letters "
        "off, transliterated, or reordered versions of an invented watch-listed party (state the invented party "
        "in the note), amounts just under and just over each approval limit, and dates on either side of a "
        "deadline. Every record is synthetic."
    ),
}

# Epic 30, US30.4: value formats per jurisdiction, so generated data looks
# like the organization's real records. Shapes only -- the model invents
# the values, and these are never real identifiers.
_FORMAT_HINTS: dict[str, str] = {
    "EU": "IBANs (two-letter country prefix + check digits), dates as DD.MM.YYYY or YYYY-MM-DD, amounts in EUR",
    "DE": "German IBANs (DE + 20 digits), 5-digit postcodes, dates DD.MM.YYYY, amounts in EUR with comma decimals",
    "FR": "French IBANs (FR76...), 5-digit postcodes, dates DD/MM/YYYY, amounts in EUR",
    "NL": "Dutch IBANs (NL + 2 digits + 4-letter bank code + 10 digits), postcodes like 1234 AB",
    "UK": "UK sort codes (NN-NN-NN) and 8-digit account numbers, National Insurance numbers shaped AA123456A (use the invalid prefix 'QQ'), postcodes like SW1A 1AA, dates DD/MM/YYYY, amounts in GBP",
    "US": "ABA routing numbers (9 digits), SSN-shaped values that start with 9 (never valid), ZIP codes, dates MM/DD/YYYY, amounts in USD",
}


def format_hints(jurisdictions: list[str]) -> str:
    hints = []
    for code in jurisdictions:
        code = code.upper()
        hint = _FORMAT_HINTS.get(code) or _FORMAT_HINTS.get(code.split("-")[0])
        if hint and hint not in hints:
            hints.append(hint)
    return "; ".join(hints)


async def generate_records(
    llm: LLMClient,
    context: TestBuilderContext,
    *,
    mode: DataMode,
    count: int,
    prompt: str | None = None,
    seed_record: dict[str, Any] | None = None,
    sample_text: str | None = None,
) -> list[GeneratedRecord]:
    if mode == "describe" and not (prompt or "").strip():
        raise TestBuilderInputError("Describe the data you want first")
    if mode == "mutate" and not seed_record:
        raise TestBuilderInputError("Pick a record to make tricky first")
    if mode == "clone" and not (sample_text or "").strip():
        raise TestBuilderInputError("Paste an example to clone first")

    parts = [
        "You generate SYNTHETIC test data for a simulated business process. Never use real people's data.\n",
        f"Agent: {context.agent_name} -- {context.purpose}\n",
        f"Each record is the input this agent receives. Fields:\n{_fields_block(context.input_fields)}\n",
        f"\n{_MODE_INSTRUCTIONS[mode]}\n",
    ]
    if context.jurisdictions and format_hints(context.jurisdictions):
        parts.append(f"\nUse the value formats of {', '.join(context.jurisdictions)}: {format_hints(context.jurisdictions)}.\n")
    if mode == "near_miss" and (context.rules or context.approval_authorities):
        parts.append(f"\n{_rules_block(context)}")
    if prompt:
        parts.append(f'\nUser\'s description: """{prompt}"""\n')
    if seed_record:
        parts.append(f"\nSeed record: {json.dumps(seed_record, default=str)}\n")
    if sample_text:
        parts.append(f'\nPasted example:\n"""{sample_text}"""\n')
    parts.append(
        f"\nGenerate {count} records. Give each a short human label (e.g. 'Large EU supplier invoice') and a "
        "one-line note on what makes it interesting.\n"
        'Respond as JSON: {"records": [{"label": str, "note": str, "record": {<field>: <value>, ...}}, ...]}'
    )
    parsed = await _complete_json(llm, "".join(parts), operation=f"twin_data_{mode}", model=GeneratedRecords)
    records = [r for r in parsed.records if r.record][:count]
    if not records:
        raise TwinServiceError("The AI didn't generate any records -- please try again")
    return records


_AMOUNT_FIELD = re.compile(r"amount|value|limit|total|sum|price|exposure", re.I)


def authority_personas(context: TestBuilderContext) -> list[ReviewerPersona]:
    """Epic 30, US30.3: one reviewer per approval-authority row with a
    monetary limit, rejecting above that limit -- the organization's real
    limits, no LLM involved. Needs a numeric input field to compare."""
    numeric = numeric_input_fields(context)
    if not numeric or context.human_checkpoint == "none":
        return []
    field = next((f for f in numeric if _AMOUNT_FIELD.search(f)), numeric[0])
    personas = []
    for authority in context.approval_authorities:
        if authority.limit_amount is None:
            continue
        limit = f"{authority.limit_amount:g} {authority.currency}".strip()
        personas.append(
            ReviewerPersona(
                name=authority.role,
                role=authority.role,
                description=f"Approves {authority.decision_type} up to {limit}; rejects anything above (from the approval-authority matrix).",
                reviewer=ReviewerSetup(
                    behaviour="threshold", field=field, threshold=authority.limit_amount, persona_name=authority.role
                ),
            )
        )
    return personas


async def generate_personas(
    llm: LLMClient, context: TestBuilderContext, *, count: int, prompt: str | None = None
) -> list[ReviewerPersona]:
    if context.human_checkpoint == "none":
        raise TwinServiceError("This agent has no human review step, so there are no reviewers to cast")
    numeric = numeric_input_fields(context)
    behaviours = (
        '"approve" (always approves), "reject" (always rejects), or "threshold" (rejects when a numeric field '
        f"exceeds a limit -- the field MUST be exactly one of: {', '.join(numeric)})"
        if numeric
        else '"approve" (always approves) or "reject" (always rejects)'
    )
    text = (
        "You cast SYNTHETIC reviewer personas for testing an AI agent's human-review step in a simulation.\n\n"
        f"{_context_block(context)}\n"
        + (f'The user wants: """{prompt}"""\n' if prompt else "")
        + f"\nCreate {count} contrasting reviewers (e.g. a rubber-stamper, a strict one, a sceptic). Each has an "
        f"invented first name + role, a one-sentence personality, and a behaviour: {behaviours}.\n"
        'Respond as JSON: {"personas": [{"name": str, "role": str, "description": str, '
        '"reviewer": {"behaviour": "approve"|"reject"|"threshold", "field": str|null, "threshold": number|null}}]}'
    )
    parsed = await _complete_json(llm, text, operation="twin_builder_personas", model=GeneratedPersonas)
    personas: list[ReviewerPersona] = []
    for persona in parsed.personas[:count]:
        reviewer = _usable_reviewer(persona.reviewer.model_dump(), context)
        reviewer.persona_name = persona.name
        personas.append(persona.model_copy(update={"reviewer": reviewer}))
    if not personas:
        raise TwinServiceError("The AI didn't cast any reviewers -- please try again")
    return personas
