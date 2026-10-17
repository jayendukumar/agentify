"""Epic 39, US39.17: a case through the whole process, not one agent.

A process execution walks the process's real flow -- the version its
deployed agents were generated from -- with Epic 16's orchestrator
(`run_orchestration`) on production ports:

- **an agent node** runs as an ordinary execution of the node's live
  deployment (`parent_execution_id` points here), inline in the walk's
  job, with every production guardrail: its identity, adapters, caps,
  circuit breakers, the kill switch. A canary takes its share of the
  cases. If the node waits for a review, the walk waits too and carries on
  when that execution ends (app/runtime/jobs.py `_after`).
- **a node no live deployment covers** is a human task in the review
  inbox (`kind="human_task"`). Resolving it records the decision and any
  outcome fields, which join the case.
- **gateways** go by the process's confirmed decision rules where it has
  any for that gateway, else the agent before it picks the branch.
- **the case file** hand-off (Epic 36): every node sees the start input
  plus every earlier node's output.

A whole process can be paused (new cases refused, running ones wait
before their next node) or killed (every deployment of the process is
killed too, and running cases stop).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import audit
from app.bpmn.nodes import FlowNodeInfo, extract_flow_nodes
from app.db.models import (
    AgentArtifactModel,
    AgentDeploymentModel,
    AgentExecutionModel,
    ProcessExecutionModel,
    ProcessRuntimeModel,
    ReviewTaskModel,
    UserModel,
    VersionModel,
)
from app.ids import new_id, utcnow
from app.jobs import queue
from app.jobs.worker import handler
from app.oracles.store import confirmed_views
from app.schemas.agents import AgentDefinition
from app.schemas.orchestration import ManualNodeConfig, NodeRun, OrchestrationScenario
from app.schemas.run import TERMINAL_RUN_STATUSES, TwinDeviation
from app.schemas.twin import TwinHumanCheckpointConfig
from app.twin.orchestrator import NodeArtifact, run_orchestration

from . import reviews
from .deployments import LIVE, kill

JOB_KIND = "runtime_process"
HELD_RETRY_SECONDS = 30
TASK_NODE_TYPES = frozenset({"task", "userTask", "serviceTask", "manualTask", "scriptTask", "businessRuleTask",
                             "sendTask", "receiveTask"})


class ProcessError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ---- what runs where ------------------------------------------------------------------------


def runtime_status(session: Session, process_id: str) -> ProcessRuntimeModel | None:
    return session.get(ProcessRuntimeModel, process_id)


def live_nodes(session: Session, process_id: str) -> dict[str, AgentDeploymentModel]:
    """Each task node a live deployment covers (the newest, if several)."""
    out: dict[str, AgentDeploymentModel] = {}
    rows = session.scalars(select(AgentDeploymentModel).where(
        AgentDeploymentModel.process_id == process_id, AgentDeploymentModel.status.in_(LIVE))
        .order_by(AgentDeploymentModel.created_at))
    for deployment in rows:
        artifact = session.get(AgentArtifactModel, deployment.agent_artifact_id)
        for node_id in (artifact.node_ids if artifact else []):
            out[node_id] = deployment
    return out


def _version(session: Session, process_id: str, nodes: dict[str, AgentDeploymentModel]) -> VersionModel:
    """The version the live agents were generated from (the newest)."""
    version_ids = set()
    for deployment in nodes.values():
        artifact = session.get(AgentArtifactModel, deployment.agent_artifact_id)
        if artifact is not None:
            version_ids.add(artifact.source_baseline_version_id)
    versions = [v for v in (session.get(VersionModel, vid) for vid in version_ids) if v and v.process_id == process_id]
    if not versions:
        raise ProcessError(409, "No agent of this process is live, so there is nothing to run a case through")
    return max(versions, key=lambda v: v.created_at)


# ---- starting a case -------------------------------------------------------------------------


def queue_walk(session: Session, execution_id: str, *, tag: str = "", delay_seconds: float = 0,
               extra: dict[str, Any] | None = None) -> None:
    queue.enqueue(session, JOB_KIND, {"process_execution_id": execution_id, **(extra or {})}, max_attempts=3,
                  delay_seconds=delay_seconds, dedupe_key=f"runtime-process:{execution_id}{tag}")


def submit(session: Session, process_id: str, *, data: dict[str, Any], idempotency_key: str, client_id: str | None,
           trigger: str = "api", requested_by: str | None = None) -> tuple[ProcessExecutionModel, bool]:
    if not idempotency_key or len(idempotency_key) > 200:
        raise ProcessError(400, "An Idempotency-Key header (up to 200 characters) is required")
    existing = session.scalar(select(ProcessExecutionModel).where(
        ProcessExecutionModel.process_id == process_id, ProcessExecutionModel.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.input != data:
            raise ProcessError(409, "This Idempotency-Key was already used with a different input")
        return existing, False
    control = runtime_status(session, process_id)
    if control is not None and control.status != "active":
        raise ProcessError(503, f"This process is {control.status}: {control.reason}")
    version = _version(session, process_id, live_nodes(session, process_id))
    execution = ProcessExecutionModel(
        id=new_id("pex"), process_id=process_id, version_id=version.id, client_id=client_id, trigger=trigger,
        idempotency_key=idempotency_key, requested_by=(requested_by or "").strip() or None, status="QUEUED",
        input=data, deviations=[], node_runs=[], visited_path=[], total_tokens=0,
    )
    session.add(execution)
    session.flush()
    queue_walk(session, execution.id)
    audit.record(session, actor=None, actor_type="service", actor_name=f"client:{client_id}" if client_id else trigger,
                 action="process_execution_submitted", object_type="process_execution", object_id=execution.id,
                 process_id=process_id, detail={"version_id": version.id, "client_id": client_id})
    return execution, True


# ---- the walk's production ports -------------------------------------------------------------


@dataclass
class NodeOutcome:
    """What the walk needs from a node run (the shape of the twin's RunOutcome)."""

    status: str
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = field(default_factory=list)
    total_cost_usd: float | None = None
    total_tokens: int = 0
    resume_state: dict[str, Any] | None = None
    steps: list = field(default_factory=list)


def _from_execution(execution: AgentExecutionModel) -> NodeOutcome:
    if execution.status not in TERMINAL_RUN_STATUSES:
        return NodeOutcome(status="WAITING_FOR_HUMAN", resume_state={"execution_id": execution.id})
    return NodeOutcome(
        status=execution.status, final_output=execution.final_output,
        deviations=[TwinDeviation.model_validate(d) for d in execution.deviations or []],
        total_cost_usd=execution.total_cost_usd, total_tokens=execution.total_tokens,
    )


class _Held(Exception):
    """The process was paused: wait before the next node."""


class ProductionNodes:
    """The walk's AgentRunner: each agent node is an execution of the
    node's live deployment, run here and now."""

    def __init__(self, session: Session, case: ProcessExecutionModel, nodes: dict[str, AgentDeploymentModel]):
        self.session, self.case, self.nodes = session, case, nodes

    async def __call__(self, node: FlowNodeInfo, artifact: NodeArtifact, inputs: dict[str, Any], *,
                       resume_state: dict[str, Any] | None, routing: tuple[str, list[str]] | None) -> NodeOutcome:
        from . import jobs

        if resume_state and resume_state.get("execution_id"):
            return _from_execution(self.session.get(AgentExecutionModel, resume_state["execution_id"]))
        self._check_running()
        child = self._new_child(node, inputs, routing)
        await jobs.run_execution(self.session, child, {"execution_id": child.id, "inline": True})
        self.session.refresh(child)
        return _from_execution(child)

    def _check_running(self) -> None:
        control = runtime_status(self.session, self.case.process_id)
        if control is not None and control.status == "paused":
            raise _Held()

    def _new_child(self, node: FlowNodeInfo, inputs: dict[str, Any],
                   routing: tuple[str, list[str]] | None) -> AgentExecutionModel:
        from . import jobs

        visit = sum(1 for n in self.case.visited_path if n == node.id)
        key = f"process:{self.case.id}:{node.id}:{visit}"
        deployment = jobs.route(self.session, self.nodes[node.id], key)
        child = jobs._new_execution(
            deployment, jobs.Caller(client_id=self.case.client_id, label=f"process:{self.case.id}", trigger="process"),
            inputs, key, parent_execution_id=self.case.id, requested_by=self.case.requested_by,
            routing={"question": routing[0], "options": routing[1]} if routing else None,
        )
        self.session.add(child)
        self.session.commit()
        return child


def _scenario(session: Session, case: ProcessExecutionModel, flow_nodes: list[FlowNodeInfo],
              nodes: dict[str, AgentDeploymentModel]) -> tuple[OrchestrationScenario, list]:
    rules = confirmed_views(session, case.process_id)
    ruled = {r.gateway_id for r in rules}
    gateways = [n for n in flow_nodes if "Gateway" in n.bpmn_type]
    people = {n.id: ManualNodeConfig(human_checkpoint_config=TwinHumanCheckpointConfig(mode="manual"))
              for n in flow_nodes if n.bpmn_type in TASK_NODE_TYPES and n.id not in nodes}
    scenario = OrchestrationScenario(
        id=case.id, process_id=case.process_id, baseline_version_id=case.version_id, created_at=case.created_at,
        name=f"Process case {case.id}", inputs=case.input or {}, handoff="case_file",
        default_gateway_routing="agent", gateway_routing={g.id: "rules" if g.id in ruled else "agent" for g in gateways},
        manual_node_config=people,
    )
    return scenario, rules


def _artifacts(nodes: dict[str, AgentDeploymentModel], flow_nodes: list[FlowNodeInfo]) -> dict[str, NodeArtifact]:
    return {
        n.id: NodeArtifact(artifact_id=nodes[n.id].agent_artifact_id,
                           definition=AgentDefinition.model_validate(nodes[n.id].snapshot["definition"]),
                           tool_schemas={})
        for n in flow_nodes if n.id in nodes and n.bpmn_type in TASK_NODE_TYPES
    }


# ---- the walk's job ------------------------------------------------------------------------------


def _stopped(session: Session, case: ProcessExecutionModel) -> bool:
    control = runtime_status(session, case.process_id)
    if control is None or control.status != "killed":
        return False
    _finish(case, "KILLED", f"The process was stopped: {control.reason}")
    return True


def _finish(case: ProcessExecutionModel, status: str, reason: str | None = None) -> None:
    case.status = status
    case.completed_at = utcnow()
    case.resume_state = None
    if reason:
        case.deviations = [*(case.deviations or []), {"reason": reason, "step_index": None}]


def _record(case: ProcessExecutionModel, outcome: Any) -> None:
    case.status = outcome.status if outcome.status != "CANCELLED" else "KILLED"
    case.node_runs = [nr.model_dump(mode="json") for nr in outcome.node_runs]
    case.visited_path = outcome.visited_path
    case.final_output = outcome.final_output
    case.deviations = [d.model_dump(mode="json") for d in outcome.deviations]
    case.total_cost_usd = outcome.total_cost_usd
    case.total_tokens = outcome.total_tokens
    case.resume_state = outcome.resume_state
    if case.status in TERMINAL_RUN_STATUSES:
        case.completed_at = utcnow()


def _waiting_on_person(session: Session, case: ProcessExecutionModel, flow_nodes: list[FlowNodeInfo]) -> None:
    """A node no agent runs: open its human task (once)."""
    state = case.resume_state or {}
    if not state.get("manual"):
        return
    node = next((n for n in flow_nodes if n.id == state["current_id"]), None)
    reviews.open_human_task(session, case, node_id=state["current_id"], label=node.label if node else state["current_id"],
                            case_data=state.get("case_data") or {})


@handler(JOB_KIND)
async def runtime_process(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from app.config import get_settings
    from app.connectors.jobs import get_job_llm

    case = session.get(ProcessExecutionModel, payload["process_execution_id"])
    if case is None or case.status in TERMINAL_RUN_STATUSES:
        return {"skipped": True}
    if _stopped(session, case):
        session.commit()
        return {"status": case.status}
    version = session.get(VersionModel, case.version_id)
    flow_nodes = extract_flow_nodes(version.xml)
    nodes = live_nodes(session, case.process_id)
    scenario, rules = _scenario(session, case, flow_nodes, nodes)
    resume_state = case.resume_state
    case.status, case.started_at = "RUNNING", case.started_at or utcnow()
    session.commit()
    try:
        outcome = await run_orchestration(
            get_job_llm(), flow_nodes=flow_nodes, node_artifacts=_artifacts(nodes, flow_nodes), scenario=scenario,
            max_turns_per_node=get_settings().runtime_max_turns,
            max_total_steps=get_settings().orchestration_max_total_steps, resume_state=resume_state,
            human_decision=payload.get("decision"), human_output=payload.get("outcome"),
            decision_rules=rules, agent_runner=ProductionNodes(session, case, nodes),
        )
    except _Held:
        case.status, case.resume_state = "QUEUED", resume_state
        queue_walk(session, case.id, tag=f":held:{utcnow().timestamp():.0f}", delay_seconds=HELD_RETRY_SECONDS)
        session.commit()
        return {"held": True}
    _record(case, outcome)
    if case.status == "WAITING_FOR_HUMAN":
        _waiting_on_person(session, case, flow_nodes)
    session.commit()
    return {"status": case.status}


# ---- a person's part -------------------------------------------------------------------------------


def resume_after_task(session: Session, task: ReviewTaskModel) -> None:
    """A human task was resolved: the case moves on with what they recorded."""
    outcome = dict(task.outcome or {})
    decision = outcome.pop("decision", None) or "approve"
    queue_walk(session, task.process_execution_id, tag=f":task:{task.id}",
               extra={"decision": decision if decision in ("approve", "reject") else "approve", "outcome": outcome})


# ---- stopping a whole process ---------------------------------------------------------------------


def set_runtime(session: Session, process_id: str, *, status: str, reason: str, user: UserModel) -> ProcessRuntimeModel:
    if status != "active" and not (reason or "").strip():
        raise ProcessError(422, "Give a reason")
    control = session.get(ProcessRuntimeModel, process_id) or ProcessRuntimeModel(process_id=process_id)
    control.status, control.reason, control.changed_by, control.changed_at = status, reason or None, user.id, utcnow()
    session.add(control)
    if status == "killed":
        _kill_everything(session, process_id, reason, user)
    audit.record(session, actor=user, action=f"process_runtime_{status}", object_type="process", object_id=process_id,
                 process_id=process_id, detail={"reason": reason})
    session.flush()
    return control


def _kill_everything(session: Session, process_id: str, reason: str, user: UserModel) -> None:
    deployments = list(session.scalars(select(AgentDeploymentModel).where(AgentDeploymentModel.process_id == process_id)))
    kill(session, deployments, reason=f"The whole process was stopped: {reason}", actor=user.id)
    for case in session.scalars(select(ProcessExecutionModel).where(
            ProcessExecutionModel.process_id == process_id,
            ProcessExecutionModel.status.not_in(TERMINAL_RUN_STATUSES))):
        _finish(case, "KILLED", f"The process was stopped: {reason}")
    for task in session.scalars(select(ReviewTaskModel).where(ReviewTaskModel.process_id == process_id,
                                                              ReviewTaskModel.kind == "human_task",
                                                              ReviewTaskModel.status == "open")):
        task.status, task.comment = "cancelled", f"The process was stopped: {reason}"


def node_runs(case: ProcessExecutionModel) -> list[NodeRun]:
    return [NodeRun.model_validate(n) for n in case.node_runs or []]
