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

Epic 17: the walk's own events (gateway decisions, entering/leaving an
automatable node, a manual node's own checkpoint, start/end) are now typed
`RunStep`s in the same flat, unified `steps` list a nested `run_scenario`
call appends to (via `on_step`) -- one trace for the whole run, not a
second parallel format. `NodeRun` summaries are kept as a per-node
convenience index over that same list, not their own trace. The walk can
now suspend (`WAITING_FOR_HUMAN`) and resume mid-node -- either inside a
nested agent's own manual checkpoint, or at a manual node's own checkpoint
-- and can be cooperatively cancelled, exactly like a standalone twin run.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.bpmn.nodes import FlowNodeInfo
from app.ids import utcnow
from app.llm import ChatMessage, LLMClient, Usage, estimate_cost_usd
from app.schemas.agents import AgentDefinition
from app.schemas.orchestration import (
    DataMappingMode,
    ManualNodeConfig,
    NodeRun,
    OrchestrationScenario,
)
from app.schemas.run import RunStatus, RunStep, StepType, TwinDeviation
from app.schemas.twin import InferredToolSchema, TwinHumanCheckpointConfig, TwinScenario

from .engine import RunCancelled, run_scenario
from .errors import TwinServiceError
from .simulators import resolve_human_decision

_MISSING = object()
_PARALLEL_GATEWAY_TYPES = {"parallelGateway"}


class _StopWalk(Exception):
    """Internal control-flow signal only -- lets the resume-continuation
    branch (which runs before the main while-loop even starts) fall
    through to the same terminal-status handling the loop's own `break`
    statements reach, without duplicating that handling."""


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
    status: RunStatus
    steps: list[RunStep] = field(default_factory=list)
    node_runs: list[NodeRun] = field(default_factory=list)
    visited_path: list[str] = field(default_factory=list)
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = field(default_factory=list)
    graded_passed: bool | None = None
    total_cost_usd: float | None = None
    total_tokens: int = 0
    resume_state: dict[str, Any] | None = None


async def _adapt_data(
    llm: LLMClient,
    *,
    to_node_id: str,
    target_fields: list,
    current_data: dict[str, Any],
    mode: DataMappingMode,
) -> tuple[dict[str, Any], tuple[Usage, str] | None]:
    """US16.5: reshape `current_data` (the previous node's output, or the
    scenario's start-event inputs for the first node) into what
    `target_fields` (the receiving agent's `input_schema`) expects. The
    before/after shapes are recorded on the caller's AGENT_DELEGATED step,
    not a separate DataHandoff record (Epic 17 folded that in)."""
    if not target_fields:
        return current_data, None

    if mode == "exact_field_contract":
        missing = [f.name for f in target_fields if f.name not in current_data]
        if missing:
            raise TwinServiceError(
                f"Exact field-name contract into node '{to_node_id}' is missing field(s) {missing} from the "
                f"upstream output (got: {sorted(current_data)})"
            )
        mapped = {f.name: current_data[f.name] for f in target_fields}
        return mapped, None

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
    return mapped, (result.usage, result.model)


def grade_orchestration(
    visited_path: list[str], final_output: dict[str, Any] | None, scenario: OrchestrationScenario
) -> tuple[bool, list[TwinDeviation]]:
    """US16.6: the run's expected path/output compared against what
    actually happened -- same "compare the real trace" principle as Epic
    14's `grade_run`, not an LLM's opinion of the run. Epic 17: returns
    `graded_passed` rather than overwriting the run's lifecycle status."""
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

    return not deviations, deviations


async def run_orchestration(
    llm: LLMClient,
    *,
    flow_nodes: list[FlowNodeInfo],
    node_artifacts: dict[str, NodeArtifact],
    scenario: OrchestrationScenario,
    max_turns_per_node: int,
    max_total_steps: int = 50,
    resume_state: dict[str, Any] | None = None,
    human_decision: Literal["approve", "reject"] | None = None,
    on_node_run: Callable[[NodeRun], Awaitable[None]] | None = None,
    on_step: Callable[[RunStep], Awaitable[None]] | None = None,
    on_phase: Callable[[RunStatus], Awaitable[None]] | None = None,
) -> OrchestrationOutcome:
    by_id = {node.id: node for node in flow_nodes}

    steps: list[RunStep] = []
    node_runs: list[NodeRun] = []
    total_cost = 0.0
    total_tokens = 0
    cost_incomplete = False

    async def emit(step_type: StepType, *, node_id: str | None = None, **kwargs: Any) -> RunStep:
        step = RunStep(seq=len(steps), type=step_type, node_id=node_id, occurred_at=utcnow(), **kwargs)
        steps.append(step)
        if on_step is not None:
            await on_step(step)
        return step

    async def emit_node_run(node_run: NodeRun) -> None:
        node_runs.append(node_run)
        if on_node_run is not None:
            await on_node_run(node_run)

    def account(usage: Usage, model: str) -> None:
        nonlocal total_cost, total_tokens, cost_incomplete
        total_tokens += usage.total_tokens
        cost = estimate_cost_usd(model, usage)
        if cost is None:
            cost_incomplete = True
        else:
            total_cost += cost

    def account_outcome(cost_usd: float | None, tokens: int) -> None:
        nonlocal total_cost, total_tokens, cost_incomplete
        total_tokens += tokens
        if cost_usd is None:
            cost_incomplete = True
        else:
            total_cost += cost_usd

    def resolve_branch(node: FlowNodeInfo) -> tuple[str | None, TwinDeviation | None]:
        """Returns (next_node_id, error_deviation). `next_node_id is None`
        with `error_deviation is None` means a clean end of the walk (not
        every diagram uses an explicit endEvent)."""
        successors = node.successors
        if len(successors) == 1:
            return successors[0].node_id, None
        if not successors:
            return None, None
        valid_targets = {s.node_id for s in successors}
        decision = scenario.gateway_decisions.get(node.id)
        if decision is None or decision.to_node_id not in valid_targets:
            return None, TwinDeviation(
                reason=f"Node '{node.id}' ({node.label}) branches to multiple flows but has no valid configured "
                f"gateway decision (options: {sorted(valid_targets)})"
            )
        return decision.to_node_id, None

    class _NodeSuspend:
        def __init__(self, manual: bool, node_resume_state: dict[str, Any] | None):
            self.manual = manual
            self.node_resume_state = node_resume_state

    async def run_task_node(
        node: FlowNodeInfo,
        current_data: dict[str, Any],
        last_node_id: str | None,
        *,
        node_resume_state: dict[str, Any] | None = None,
        manual_resume: bool = False,
    ) -> tuple[NodeRun | None, dict[str, Any], _NodeSuspend | None]:
        """Executes (or resumes) one task-type node -- an automatable
        agent, or a manual step -- returning either a completed NodeRun +
        the walk's next `current_data`, or a suspend signal to propagate up
        as the whole orchestration run's own WAITING_FOR_HUMAN."""
        artifact = node_artifacts.get(node.id)
        started = utcnow()
        steps_before = len(steps)

        if artifact is not None:
            if node_resume_state is not None:
                outcome = await run_scenario(
                    llm, artifact=artifact.definition, tool_schemas=artifact.tool_schemas,
                    scenario=TwinScenario(
                        id="orchestration-inline", agent_artifact_id=artifact.artifact_id, name=node.label,
                        inputs=current_data, system_stubs=scenario.system_stubs.get(node.id, {}),
                        human_checkpoint_config=scenario.human_checkpoint_config.get(node.id, TwinHumanCheckpointConfig()),
                        expected_steps=[], expected_outputs={}, created_at=utcnow(),
                    ),
                    max_turns=max_turns_per_node, resume_state=node_resume_state, human_decision=human_decision,
                    node_id=node.id, on_step=on_step, on_phase=on_phase,
                )
            else:
                mapping_mode = scenario.data_mapping_mode.get(node.id, "llm_adapter")
                mapped_input, adapter_usage = await _adapt_data(
                    llm, to_node_id=node.id, target_fields=artifact.definition.input_schema,
                    current_data=current_data, mode=mapping_mode,
                )
                if adapter_usage is not None:
                    account(*adapter_usage)
                await emit(
                    "AGENT_DELEGATED", node_id=node.id, target=node.id,
                    arguments={"from_node_id": last_node_id, "mode": mapping_mode, "input_before": current_data},
                    result={"input_after": mapped_input},
                )
                node_scenario = TwinScenario(
                    id="orchestration-inline", agent_artifact_id=artifact.artifact_id, name=node.label,
                    inputs=mapped_input, system_stubs=scenario.system_stubs.get(node.id, {}),
                    human_checkpoint_config=scenario.human_checkpoint_config.get(node.id, TwinHumanCheckpointConfig()),
                    expected_steps=[], expected_outputs={}, created_at=utcnow(),
                )
                outcome = await run_scenario(
                    llm, artifact=artifact.definition, tool_schemas=artifact.tool_schemas, scenario=node_scenario,
                    max_turns=max_turns_per_node, node_id=node.id, on_step=on_step, on_phase=on_phase,
                )

            account_outcome(outcome.total_cost_usd, outcome.total_tokens)
            if outcome.status == "WAITING_FOR_HUMAN":
                return None, current_data, _NodeSuspend(manual=False, node_resume_state=outcome.resume_state)

            if outcome.status != "COMPLETED":
                error_message = "; ".join(d.reason for d in outcome.deviations) or "agent run failed"
                node_run = NodeRun(
                    node_id=node.id, node_label=node.label, kind="agent", status=outcome.status,
                    agent_artifact_id=artifact.artifact_id, steps=outcome.steps, output=outcome.final_output,
                    error_message=error_message, total_cost_usd=outcome.total_cost_usd,
                    total_tokens=outcome.total_tokens, started_at=started, completed_at=utcnow(),
                )
                return node_run, current_data, None

            node_run = NodeRun(
                node_id=node.id, node_label=node.label, kind="agent", status=outcome.status,
                agent_artifact_id=artifact.artifact_id, steps=outcome.steps, output=outcome.final_output,
                total_cost_usd=outcome.total_cost_usd, total_tokens=outcome.total_tokens,
                started_at=started, completed_at=utcnow(),
            )
            return node_run, (outcome.final_output or {}), None

        # Manual (non-automatable) node.
        manual_cfg = scenario.manual_node_config.get(node.id) or ManualNodeConfig()
        if manual_cfg.mode == "fixed_stub":
            output = dict(manual_cfg.fixed_output)
            await emit("TOOL_EXECUTED", node_id=node.id, target=node.id, result=output)
        else:
            if not manual_resume:
                await emit("HUMAN_APPROVAL_REQUESTED", node_id=node.id, target=node.id, arguments=current_data)
                if manual_cfg.human_checkpoint_config.mode == "manual" and human_decision is None:
                    return None, current_data, _NodeSuspend(manual=True, node_resume_state=None)
            if manual_resume and human_decision is not None:
                decision = human_decision
            else:
                rng = random.Random(manual_cfg.human_checkpoint_config.seed)
                decision = resolve_human_decision(current_data, manual_cfg.human_checkpoint_config, rng)
            await emit(
                "HUMAN_APPROVED" if decision == "approve" else "HUMAN_REJECTED",
                node_id=node.id, target=node.id, decision=decision,
            )
            output = {**current_data, "human_decision": decision}
        node_run = NodeRun(
            node_id=node.id, node_label=node.label, kind="manual", status="COMPLETED",
            steps=[s for s in steps[steps_before:] if s.node_id == node.id], output=output,
            started_at=started, completed_at=utcnow(),
        )
        return node_run, output, None

    def build_resume_state(
        *, current_id: str, current_data: dict[str, Any], visited_path: list[str], last_node_id: str | None,
        suspend: "_NodeSuspend",
    ) -> dict[str, Any]:
        return {
            "current_id": current_id,
            "current_data": current_data,
            "visited_path": visited_path,
            "last_node_id": last_node_id,
            "node_runs": [nr.model_dump(mode="json") for nr in node_runs],
            "steps": [s.model_dump(mode="json") for s in steps],
            "manual": suspend.manual,
            "node_resume_state": suspend.node_resume_state,
        }

    terminal_status: RunStatus | None = None
    terminal_deviation: TwinDeviation | None = None
    final_output: dict[str, Any] | None = None
    visited_path: list[str] = []

    try:
        if resume_state is not None:
            current_id = resume_state["current_id"]
            current_data = resume_state["current_data"]
            visited_path: list[str] = list(resume_state["visited_path"])
            last_node_id = resume_state["last_node_id"]
            node_runs.extend(NodeRun.model_validate(nr) for nr in resume_state["node_runs"])
            steps.extend(RunStep.model_validate(s) for s in resume_state["steps"])
            node = by_id[current_id]
            node_run, current_data, suspend = await run_task_node(
                node, current_data, last_node_id,
                node_resume_state=resume_state.get("node_resume_state"),
                manual_resume=bool(resume_state.get("manual")),
            )
            if suspend is not None:
                return OrchestrationOutcome(
                    status="WAITING_FOR_HUMAN", steps=steps, node_runs=node_runs, visited_path=visited_path,
                    resume_state=build_resume_state(
                        current_id=current_id, current_data=current_data, visited_path=visited_path,
                        last_node_id=last_node_id, suspend=suspend,
                    ),
                )
            assert node_run is not None
            await emit_node_run(node_run)
            if node_run.status != "COMPLETED":
                terminal_status = node_run.status
                terminal_deviation = TwinDeviation(reason=f"Node '{node.id}' ({node.label}): {node_run.error_message}")
                raise _StopWalk()
            next_id, error_deviation = resolve_branch(node)
            if error_deviation is not None:
                terminal_status = "FAILED"
                terminal_deviation = error_deviation
                raise _StopWalk()
            if next_id is None:
                final_output = current_data
                raise _StopWalk()
            last_node_id = node.id
            current_id = next_id
        else:
            start_nodes = [node for node in flow_nodes if node.bpmn_type == "startEvent"]
            if len(start_nodes) > 1:
                raise TwinServiceError(
                    f"Process diagram has multiple start events ({sorted(n.id for n in start_nodes)}) -- ambiguous "
                    "where to begin a single-threaded rehearsal run"
                )
            if start_nodes:
                entry_node_id = start_nodes[0].id
            else:
                # Finalize only checks XML well-formedness, not narrative
                # completeness -- a finalized diagram authored as plain
                # tasks/gateways with no explicit startEvent/endEvent is
                # real, valid data (found by testing against a real
                # finalized process). Fall back to whichever node has no
                # incoming flow at all.
                root_nodes = [node for node in flow_nodes if not node.predecessors]
                if not root_nodes:
                    raise TwinServiceError(
                        "Process diagram has no start event and no node without an incoming flow to begin an "
                        "orchestration run from"
                    )
                if len(root_nodes) > 1:
                    raise TwinServiceError(
                        f"Process diagram has no start event and multiple candidate entry nodes with no incoming "
                        f"flow ({sorted(n.id for n in root_nodes)}) -- ambiguous where to begin a single-threaded "
                        "rehearsal run"
                    )
                entry_node_id = root_nodes[0].id

            visited_path = []
            current_data = dict(scenario.inputs)
            last_node_id = None
            current_id = entry_node_id
            await emit("RUN_STARTED")

        steps_walked = len(visited_path)
        while True:
            steps_walked += 1
            if steps_walked > max_total_steps:
                terminal_status = "FAILED"
                terminal_deviation = TwinDeviation(
                    reason=f"Exceeded max total steps ({max_total_steps}) without reaching an end event -- likely a "
                    "rework loop with no exit configured"
                )
                break

            node = by_id.get(current_id)
            if node is None:
                terminal_status = "FAILED"
                terminal_deviation = TwinDeviation(reason=f"Walk reached an unknown node id '{current_id}'")
                break
            visited_path.append(node.id)

            if node.bpmn_type == "startEvent":
                next_id, error_deviation = resolve_branch(node)
                if next_id is None:
                    terminal_status = "FAILED"
                    terminal_deviation = error_deviation or TwinDeviation(
                        reason=f"Start event '{node.id}' ({node.label}) has no outgoing flow"
                    )
                    break
                last_node_id = node.id
                current_id = next_id
                continue

            if node.bpmn_type == "endEvent":
                final_output = current_data
                started = utcnow()
                await emit("RUN_COMPLETED", node_id=node.id, target=node.id, result=current_data)
                await emit_node_run(
                    NodeRun(
                        node_id=node.id, node_label=node.label, kind="end_event", status="COMPLETED",
                        output=current_data, started_at=started, completed_at=started,
                    )
                )
                terminal_status = "COMPLETED"
                break

            if "Gateway" in node.bpmn_type:
                if node.bpmn_type in _PARALLEL_GATEWAY_TYPES:
                    terminal_status = "FAILED"
                    terminal_deviation = TwinDeviation(
                        reason=f"Gateway '{node.id}' is a parallel gateway -- not supported by this epic's first "
                        "slice (see planning/epics/16-orchestration-rehearsal.md)"
                    )
                    break
                next_id, error_deviation = resolve_branch(node)
                started = utcnow()
                await emit("DECISION", node_id=node.id, target=node.id, result={"to_node_id": next_id})
                await emit_node_run(
                    NodeRun(
                        node_id=node.id, node_label=node.label, kind="gateway", status="COMPLETED",
                        output=current_data, started_at=started, completed_at=started,
                    )
                )
                if error_deviation is not None:
                    terminal_status = "FAILED"
                    terminal_deviation = error_deviation
                    break
                if next_id is None:
                    # No outgoing flow: a clean end of the walk.
                    final_output = current_data
                    terminal_status = "COMPLETED"
                    break
                last_node_id = node.id
                current_id = next_id
                continue

            # Task-type node: either an automatable agent or a manual step.
            try:
                node_run, current_data, suspend = await run_task_node(node, current_data, last_node_id)
            except TwinServiceError as exc:
                artifact = node_artifacts.get(node.id)
                node_run = NodeRun(
                    node_id=node.id, node_label=node.label, kind="agent" if artifact is not None else "manual",
                    status="FAILED", agent_artifact_id=artifact.artifact_id if artifact is not None else None,
                    error_message=str(exc), started_at=utcnow(), completed_at=utcnow(),
                )
                await emit_node_run(node_run)
                terminal_status = "FAILED"
                terminal_deviation = TwinDeviation(reason=f"Node '{node.id}' ({node.label}): {exc}")
                break

            if suspend is not None:
                return OrchestrationOutcome(
                    status="WAITING_FOR_HUMAN", steps=steps, node_runs=node_runs, visited_path=visited_path,
                    resume_state=build_resume_state(
                        current_id=current_id, current_data=current_data, visited_path=visited_path,
                        last_node_id=last_node_id, suspend=suspend,
                    ),
                )

            assert node_run is not None
            await emit_node_run(node_run)
            if node_run.status not in ("COMPLETED",):
                terminal_status = node_run.status
                terminal_deviation = TwinDeviation(reason=f"Node '{node.id}' ({node.label}): {node_run.error_message}")
                break

            next_id, error_deviation = resolve_branch(node)
            if error_deviation is not None:
                terminal_status = "FAILED"
                terminal_deviation = error_deviation
                break
            if next_id is None:
                final_output = current_data
                terminal_status = "COMPLETED"
                break
            last_node_id = node.id
            current_id = next_id
    except _StopWalk:
        pass
    except RunCancelled:
        terminal_status = "CANCELLED"
        terminal_deviation = TwinDeviation(reason="Run was cancelled")
    except TwinServiceError as exc:
        terminal_status = "FAILED"
        terminal_deviation = TwinDeviation(reason=str(exc))

    deviations: list[TwinDeviation] = []
    graded_passed: bool | None = None
    if terminal_status == "COMPLETED":
        graded_passed, grading_deviations = grade_orchestration(visited_path, final_output, scenario)
        deviations = grading_deviations
    elif terminal_deviation is not None:
        deviations = [terminal_deviation]

    return OrchestrationOutcome(
        status=terminal_status or "FAILED",
        steps=steps,
        node_runs=node_runs,
        visited_path=visited_path,
        final_output=final_output,
        deviations=deviations,
        graded_passed=graded_passed,
        total_cost_usd=None if cost_incomplete else total_cost,
        total_tokens=total_tokens,
    )
