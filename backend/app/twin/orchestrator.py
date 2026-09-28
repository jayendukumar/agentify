"""Epic 16: walks a process's real flow graph, running each automatable
node's generated agent (via Epic 14's `run_scenario`) in sequence and
simulating non-automatable nodes and gateways along the way, so a whole
blueprint's worth of agents can be rehearsed together rather than one at a
time. See planning/epics/16-orchestration-rehearsal.md for the full design
and the locked decisions this implements.

Deliberately reuses Epic 14's engine/simulators as the per-node execution
primitive (`run_scenario`, `resolve_human_decision`) instead of
duplicating their tool-calling/checkpoint logic -- this module only adds
the graph walk, gateway resolution, manual-node resolution, and the
cross-agent data adapter around them.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.bpmn.nodes import FlowNodeInfo
from app.ids import utcnow
from app.llm import ChatMessage, LLMClient, Usage, estimate_cost_usd
from app.schemas.agents import AgentDefinition
from app.schemas.orchestration import (
    DataHandoff,
    DataMappingMode,
    ManualNodeConfig,
    NodeRun,
    OrchestrationRunStatus,
    OrchestrationScenario,
)
from app.schemas.twin import InferredToolSchema, TwinDeviation, TwinHumanCheckpointConfig, TwinScenario, TwinTraceStep

from .engine import run_scenario
from .errors import TwinServiceError
from .simulators import resolve_human_decision

_MISSING = object()
_PARALLEL_GATEWAY_TYPES = {"parallelGateway"}


@dataclass
class NodeArtifact:
    """The runnable agent for one automatable blueprint node/group, plus
    its already-resolved (or freshly inferred) tool schemas -- callers
    (app/db/repository.py) assemble one of these per node id that has a
    generated `AgentArtifact`, reusing Epic 14's schema-inference cache."""

    artifact_id: str
    definition: AgentDefinition
    tool_schemas: dict[str, InferredToolSchema]


@dataclass
class OrchestrationOutcome:
    status: OrchestrationRunStatus
    node_runs: list[NodeRun] = field(default_factory=list)
    handoffs: list[DataHandoff] = field(default_factory=list)
    visited_path: list[str] = field(default_factory=list)
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = field(default_factory=list)
    total_cost_usd: float | None = None
    total_tokens: int = 0


async def _adapt_data(
    llm: LLMClient,
    *,
    from_node_id: str | None,
    to_node_id: str,
    target_fields: list,
    current_data: dict[str, Any],
    mode: DataMappingMode,
) -> tuple[dict[str, Any], DataHandoff, tuple[Usage, str] | None]:
    """US16.5: reshape `current_data` (the previous node's output, or the
    scenario's start-event inputs for the first node) into what
    `target_fields` (the receiving agent's `input_schema`) expects."""
    if not target_fields:
        handoff = DataHandoff(
            from_node_id=from_node_id, to_node_id=to_node_id, mode=mode, input_before=current_data, input_after=current_data
        )
        return current_data, handoff, None

    if mode == "exact_field_contract":
        missing = [f.name for f in target_fields if f.name not in current_data]
        if missing:
            raise TwinServiceError(
                f"Exact field-name contract into node '{to_node_id}' is missing field(s) {missing} from the "
                f"upstream output (got: {sorted(current_data)})"
            )
        mapped = {f.name: current_data[f.name] for f in target_fields}
        handoff = DataHandoff(
            from_node_id=from_node_id, to_node_id=to_node_id, mode=mode, input_before=current_data, input_after=mapped
        )
        return mapped, handoff, None

    field_descriptions = [f"{f.name} ({f.format}, {f.source_or_destination})" for f in target_fields]
    prompt = (
        "Reshape the following JSON data to match the target field list exactly, for one step of a "
        "multi-agent test rehearsal. Use the source data's values where they clearly correspond; use null "
        "for any target field you cannot derive from the source data -- never fabricate a value.\n\n"
        f"Source data: {json.dumps(current_data)}\n\n"
        f"Target fields: {field_descriptions}\n\n"
        f"Return ONLY a JSON object with exactly these keys: {[f.name for f in target_fields]}."
    )
    result = await llm.complete(
        [ChatMessage(role="system", content=prompt)],
        operation="orchestration_data_adapter",
        response_format={"type": "json_object"},
        max_tokens=1000,
    )
    if not result.text:
        raise TwinServiceError(f"LLM returned no content while adapting data for node '{to_node_id}'")
    try:
        mapped = json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise TwinServiceError(f"LLM returned invalid JSON while adapting data for node '{to_node_id}': {exc}") from exc
    if not isinstance(mapped, dict):
        raise TwinServiceError(f"LLM's data adaptation for node '{to_node_id}' was not a JSON object")
    handoff = DataHandoff(
        from_node_id=from_node_id, to_node_id=to_node_id, mode=mode, input_before=current_data, input_after=mapped
    )
    return mapped, handoff, (result.usage, result.model)


def grade_orchestration(
    visited_path: list[str], final_output: dict[str, Any] | None, scenario: OrchestrationScenario
) -> tuple[OrchestrationRunStatus, list[TwinDeviation]]:
    """US16.6: the run's expected path/output compared against what
    actually happened -- same "compare the real trace" principle as Epic
    14's `grade_run`, not an LLM's opinion of the run."""
    deviations: list[TwinDeviation] = []

    if scenario.expected_path:
        for i in range(max(len(scenario.expected_path), len(visited_path))):
            expected = scenario.expected_path[i] if i < len(scenario.expected_path) else None
            actual = visited_path[i] if i < len(visited_path) else None
            if expected != actual:
                deviations.append(
                    TwinDeviation(reason=f"Path step {i}: expected node '{expected}', visited '{actual}'", step_index=i)
                )

    for key, expected_value in scenario.expected_final_output.items():
        actual_value = (final_output or {}).get(key, _MISSING)
        if actual_value is _MISSING:
            deviations.append(TwinDeviation(reason=f"Expected final output field '{key}' was missing from the final output"))
        elif expected_value is not None and actual_value != expected_value:
            deviations.append(
                TwinDeviation(reason=f"Expected final output field '{key}' was '{actual_value}', expected '{expected_value}'")
            )

    status: OrchestrationRunStatus = "passed" if not deviations else "failed"
    return status, deviations


async def run_orchestration(
    llm: LLMClient,
    *,
    flow_nodes: list[FlowNodeInfo],
    node_artifacts: dict[str, NodeArtifact],
    scenario: OrchestrationScenario,
    max_turns_per_node: int,
    max_total_steps: int = 50,
    on_node_run: Callable[[NodeRun], Awaitable[None]] | None = None,
) -> OrchestrationOutcome:
    by_id = {node.id: node for node in flow_nodes}
    start_nodes = [node for node in flow_nodes if node.bpmn_type == "startEvent"]
    if len(start_nodes) > 1:
        raise TwinServiceError(
            f"Process diagram has multiple start events ({sorted(n.id for n in start_nodes)}) -- ambiguous "
            "where to begin a single-threaded rehearsal run"
        )
    if start_nodes:
        entry_node_id = start_nodes[0].id
    else:
        # Finalize only checks XML well-formedness (app/bpmn/validation.py's
        # validate_bpmn_integrity), not narrative completeness -- a
        # finalized diagram authored as plain tasks/gateways with no
        # explicit startEvent/endEvent is real, valid data (found by
        # testing against a real finalized process; see
        # planning/decision-log.md's 2026-09-28 entry). Fall back to
        # whichever node has no incoming flow at all.
        root_nodes = [node for node in flow_nodes if not node.predecessors]
        if not root_nodes:
            raise TwinServiceError(
                "Process diagram has no start event and no node without an incoming flow to begin an "
                "orchestration run from"
            )
        if len(root_nodes) > 1:
            raise TwinServiceError(
                f"Process diagram has no start event and multiple candidate entry nodes with no incoming flow "
                f"({sorted(n.id for n in root_nodes)}) -- ambiguous where to begin a single-threaded rehearsal run"
            )
        entry_node_id = root_nodes[0].id

    node_runs: list[NodeRun] = []
    handoffs: list[DataHandoff] = []
    visited_path: list[str] = []
    current_data: dict[str, Any] = dict(scenario.inputs)
    total_cost = 0.0
    total_tokens = 0
    cost_incomplete = False
    final_output: dict[str, Any] | None = None
    last_node_id: str | None = None
    run_status: OrchestrationRunStatus = "running"
    error_deviation: TwinDeviation | None = None

    def account(usage: Usage, model: str) -> None:
        nonlocal total_cost, total_tokens, cost_incomplete
        total_tokens += usage.total_tokens
        cost = estimate_cost_usd(model, usage)
        if cost is None:
            cost_incomplete = True
        else:
            total_cost += cost

    async def emit(node_run: NodeRun) -> None:
        node_runs.append(node_run)
        if on_node_run is not None:
            await on_node_run(node_run)

    def resolve_branch(node: FlowNodeInfo) -> str | None:
        """Returns the next node id, or None if the walk should stop at
        this node. None has two distinct meanings the caller must tell
        apart via whether `error_deviation` got set: a node with no
        outgoing flow at all is a clean, successful end of the walk (not
        every diagram uses an explicit endEvent -- see this function's
        caller for why), while a node with multiple outgoing flows and no
        resolvable gateway decision is an error."""
        nonlocal error_deviation, run_status
        successors = node.successors
        if len(successors) == 1:
            return successors[0].node_id
        if not successors:
            return None
        valid_targets = {s.node_id for s in successors}
        decision = scenario.gateway_decisions.get(node.id)
        if decision is None or decision.to_node_id not in valid_targets:
            error_deviation = TwinDeviation(
                reason=f"Node '{node.id}' ({node.label}) branches to multiple flows but has no valid configured "
                f"gateway decision (options: {sorted(valid_targets)})"
            )
            run_status = "error"
            return None
        return decision.to_node_id

    current_id = entry_node_id
    steps = 0
    while True:
        steps += 1
        if steps > max_total_steps:
            error_deviation = TwinDeviation(
                reason=f"Exceeded max total steps ({max_total_steps}) without reaching an end event -- likely a "
                "rework loop with no exit configured"
            )
            run_status = "error"
            break

        node = by_id.get(current_id)
        if node is None:
            error_deviation = TwinDeviation(reason=f"Walk reached an unknown node id '{current_id}'")
            run_status = "error"
            break
        visited_path.append(node.id)

        if node.bpmn_type == "startEvent":
            next_id = resolve_branch(node)
            if next_id is None:
                if error_deviation is None:
                    error_deviation = TwinDeviation(reason=f"Start event '{node.id}' ({node.label}) has no outgoing flow")
                    run_status = "error"
                break
            last_node_id = node.id
            current_id = next_id
            continue

        if node.bpmn_type == "endEvent":
            final_output = current_data
            started = utcnow()
            await emit(
                NodeRun(
                    node_id=node.id, node_label=node.label, kind="end_event", status="passed",
                    output=current_data, started_at=started, completed_at=started,
                )
            )
            run_status = "passed"
            break

        if "Gateway" in node.bpmn_type:
            if node.bpmn_type in _PARALLEL_GATEWAY_TYPES:
                error_deviation = TwinDeviation(
                    reason=f"Gateway '{node.id}' is a parallel gateway -- not supported by this epic's first "
                    "slice (see planning/epics/16-orchestration-rehearsal.md)"
                )
                run_status = "error"
                break
            started = utcnow()
            await emit(
                NodeRun(
                    node_id=node.id, node_label=node.label, kind="gateway", status="passed",
                    output=current_data, started_at=started, completed_at=started,
                )
            )
            next_id = resolve_branch(node)
            if next_id is None:
                # No outgoing flow: a clean end of the walk, not every
                # diagram uses an explicit endEvent (see this function's
                # entry-point fallback above for why).
                if error_deviation is None:
                    final_output = current_data
                break
            last_node_id = node.id
            current_id = next_id
            continue

        # Task-type node: either an automatable agent or a manual step.
        artifact = node_artifacts.get(node.id)
        started = utcnow()
        try:
            if artifact is not None:
                mapping_mode = scenario.data_mapping_mode.get(node.id, "llm_adapter")
                mapped_input, handoff, adapter_usage = await _adapt_data(
                    llm,
                    from_node_id=last_node_id,
                    to_node_id=node.id,
                    target_fields=artifact.definition.input_schema,
                    current_data=current_data,
                    mode=mapping_mode,
                )
                handoffs.append(handoff)
                if adapter_usage is not None:
                    account(*adapter_usage)

                node_scenario = TwinScenario(
                    id="orchestration-inline",
                    agent_artifact_id=artifact.artifact_id,
                    name=node.label,
                    inputs=mapped_input,
                    system_stubs=scenario.system_stubs.get(node.id, {}),
                    human_checkpoint_config=scenario.human_checkpoint_config.get(node.id, TwinHumanCheckpointConfig()),
                    expected_steps=[],
                    expected_outputs={},
                    created_at=utcnow(),
                )
                outcome = await run_scenario(
                    llm,
                    artifact=artifact.definition,
                    tool_schemas=artifact.tool_schemas,
                    scenario=node_scenario,
                    max_turns=max_turns_per_node,
                )
                total_tokens += outcome.total_tokens
                if outcome.total_cost_usd is None:
                    cost_incomplete = True
                else:
                    total_cost += outcome.total_cost_usd

                if outcome.status == "error":
                    error_message = "; ".join(d.reason for d in outcome.deviations) or "agent run failed"
                    node_run = NodeRun(
                        node_id=node.id, node_label=node.label, kind="agent", status="error",
                        agent_artifact_id=artifact.artifact_id, trace=outcome.trace, output=outcome.final_output,
                        error_message=error_message, total_cost_usd=outcome.total_cost_usd,
                        total_tokens=outcome.total_tokens, started_at=started, completed_at=utcnow(),
                    )
                    await emit(node_run)
                    error_deviation = TwinDeviation(reason=f"Node '{node.id}' ({node.label}): {error_message}")
                    run_status = "error"
                    break

                node_run = NodeRun(
                    node_id=node.id, node_label=node.label, kind="agent", status="passed",
                    agent_artifact_id=artifact.artifact_id, trace=outcome.trace, output=outcome.final_output,
                    total_cost_usd=outcome.total_cost_usd, total_tokens=outcome.total_tokens,
                    started_at=started, completed_at=utcnow(),
                )
                await emit(node_run)
                current_data = outcome.final_output or {}
            else:
                manual_cfg = scenario.manual_node_config.get(node.id) or ManualNodeConfig()
                trace: list[TwinTraceStep] = []
                if manual_cfg.mode == "fixed_stub":
                    output = dict(manual_cfg.fixed_output)
                else:
                    rng = random.Random(manual_cfg.human_checkpoint_config.seed)
                    decision = resolve_human_decision(current_data, manual_cfg.human_checkpoint_config, rng)
                    trace.append(TwinTraceStep(kind="human_checkpoint", target=node.id, arguments=current_data, decision=decision))
                    output = {**current_data, "human_decision": decision}
                node_run = NodeRun(
                    node_id=node.id, node_label=node.label, kind="manual", status="passed",
                    trace=trace, output=output, started_at=started, completed_at=utcnow(),
                )
                await emit(node_run)
                current_data = output
        except TwinServiceError as exc:
            node_run = NodeRun(
                node_id=node.id, node_label=node.label, kind="agent" if artifact is not None else "manual",
                status="error", agent_artifact_id=artifact.artifact_id if artifact is not None else None,
                error_message=str(exc), started_at=started, completed_at=utcnow(),
            )
            await emit(node_run)
            error_deviation = TwinDeviation(reason=f"Node '{node.id}' ({node.label}): {exc}")
            run_status = "error"
            break

        next_id = resolve_branch(node)
        if next_id is None:
            # No outgoing flow: a clean end of the walk (see the gateway
            # branch above for why this isn't automatically an error).
            if error_deviation is None:
                final_output = current_data
            break
        last_node_id = node.id
        current_id = next_id

    deviations: list[TwinDeviation] = []
    if error_deviation is not None:
        deviations.append(error_deviation)
    else:
        run_status, grading_deviations = grade_orchestration(visited_path, final_output, scenario)
        deviations.extend(grading_deviations)

    return OrchestrationOutcome(
        status=run_status,
        node_runs=node_runs,
        handoffs=handoffs,
        visited_path=visited_path,
        final_output=final_output,
        deviations=deviations,
        total_cost_usd=None if cost_incomplete else total_cost,
        total_tokens=total_tokens,
    )
