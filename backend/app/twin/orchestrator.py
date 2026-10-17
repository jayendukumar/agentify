"""Epic 16: a rehearsal of a whole process in the digital twin -- the
process walk (app/runtime/walk.py, shared with production since Epic 39,
US39.17) with the twin's ports: each agent node runs Epic 14's
`run_scenario` with simulated tools, and a person's step is simulated by
`resolve_human_decision` unless the scenario makes it manual.

See planning/epics/16-orchestration-rehearsal.md for the design.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal

from app.bpmn.nodes import FlowNodeInfo
from app.ids import utcnow
from app.llm import LLMClient
from app.oracles.rules import RuleView
from app.runtime.walk import (  # noqa: F401 (the walk's helpers, re-exported for the twin's callers)
    CASE_FILE_FIELD,
    AgentRunner,
    NodeArtifact,
    NodeContext,
    OrchestrationOutcome,
    branch_options,
    first_matching_rule,
    grade_orchestration,
    match_branch,
    run_walk,
)
from app.schemas.environment import TwinEnvironment
from app.schemas.orchestration import NodeRun, OrchestrationScenario
from app.schemas.run import RunStatus, RunStep
from app.schemas.twin import TwinScenario

from .engine import run_scenario
from .simulators import resolve_human_decision


def _twin_runner(llm: LLMClient, scenario: OrchestrationScenario, *, max_turns: int,
                 environment: TwinEnvironment | None,
                 on_phase: Callable[[RunStatus], Awaitable[None]] | None) -> AgentRunner:
    async def run(node: FlowNodeInfo, artifact: NodeArtifact, inputs: dict[str, Any], *,
                  resume_state: dict[str, Any] | None, routing: tuple[str, list[str]] | None,
                  context: NodeContext) -> Any:
        node_scenario = TwinScenario(
            id="orchestration-inline", agent_artifact_id=artifact.artifact_id, name=node.label, inputs=inputs,
            system_stubs=scenario.system_stubs.get(node.id, {}), human_checkpoint_config=context.checkpoint,
            expected_steps=[], expected_outputs={}, created_at=utcnow(),
        )
        return await run_scenario(
            llm, artifact=artifact.definition, tool_schemas=artifact.tool_schemas, scenario=node_scenario,
            max_turns=max_turns, environment=environment, resume_state=resume_state,
            human_decision=context.human_decision, node_id=node.id, on_step=context.relay, on_phase=on_phase,
            routing=routing,
        )

    return run


async def run_orchestration(
    llm: LLMClient,
    *,
    flow_nodes: list[FlowNodeInfo],
    node_artifacts: dict[str, NodeArtifact],
    scenario: OrchestrationScenario,
    max_turns_per_node: int,
    max_total_steps: int = 50,
    environment: TwinEnvironment | None = None,
    resume_state: dict[str, Any] | None = None,
    human_decision: Literal["approve", "reject"] | None = None,
    on_node_run: Callable[[NodeRun], Awaitable[None]] | None = None,
    on_step: Callable[[RunStep], Awaitable[None]] | None = None,
    on_phase: Callable[[RunStatus], Awaitable[None]] | None = None,
    decision_rules: list[RuleView] | None = None,
    seed: int | None = None,
) -> OrchestrationOutcome:
    """A twin rehearsal run of the whole process. Epic 36: `decision_rules`
    are the process's confirmed rules, used by `rules`-routed gateways;
    `seed` (a batch case's own) seeds every manual node and reviewer that
    doesn't set its own, so a case replays exactly."""
    return await run_walk(
        llm, flow_nodes=flow_nodes, node_artifacts=node_artifacts, scenario=scenario,
        max_turns_per_node=max_turns_per_node, max_total_steps=max_total_steps, environment=environment,
        resume_state=resume_state, human_decision=human_decision, on_node_run=on_node_run, on_step=on_step,
        on_phase=on_phase, decision_rules=decision_rules, seed=seed,
        agent_runner=_twin_runner(llm, scenario, max_turns=max_turns_per_node, environment=environment,
                                  on_phase=on_phase),
        decide_person=resolve_human_decision,
    )
