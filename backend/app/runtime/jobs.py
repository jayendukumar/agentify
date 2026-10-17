"""Epic 39, US39.3/US39.4/US39.8-39.12: running one execution.

`submit` takes a case from a tenant's system and queues it as a
`runtime_execution` job, after checking the deployment's caps (US39.9:
429 with the reason when one is reached) and the input against the input
schema (app/runtime/inputs.py: 422). Internal triggers reach `submit`
through a `runtime_submit` job (`queue_submission`). The handler runs a
case through the shared executor with production ports:

- tools: app/runtime/adapters.py, bound per deployment, behind the
  deployment's circuit breakers;
- humans: no one decides on the spot, so a checkpoint suspends the case
  as WAITING_FOR_HUMAN and opens a task in the review inbox (US39.10).
  A reviewer's decision queues the same job again with the decision,
  which resumes the case where it stopped;
- steps: appended to `execution_steps` and committed one by one, so a
  crash loses none that happened.

A case that ends ESCALATED opens an escalation task. After every case,
the pause rules are checked (US39.12), and a case that ended is
reported to its callback URL (app/runtime/callbacks.py). While a deployment is paused, new
cases are refused, or held and retried every 30 seconds when it queues
instead (`when_paused`); cases already running finish.

Before every model call and tool call (`on_phase`), the handler reads the
deployment and identity status from the database. If the deployment was
killed, retired or its identity revoked, the run stops right there as
KILLED. A tool call already sent is allowed to finish; nothing runs
after it.

Restarts: a job whose worker died is claimed again after its lease. If
the dead attempt had already sent a call that changes data, its outcome
is unknown, so the execution ends ESCALATED rather than risk sending it
twice. Otherwise the case starts again as a new attempt.

US39.13, routing: a case sent to any deployment of an agent goes to the
version that serves it -- the canary for its share of idempotency-key
hashes, else the stable version -- and a copy goes to a version in shadow,
as an execution of its own (`shadow_of`). A shadow case sends nothing
that changes data, opens no review task and calls no callback: a
checkpoint is approved on the spot, since nothing would be sent anyway.
The same idempotency key finds the case on whichever version took it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import audit
from app.config import get_settings
from app.db.models import (
    AgentDeploymentModel,
    AgentExecutionModel,
    AgentIdentityModel,
    ContextEntityModel,
    ExecutionStepModel,
)
from app.ids import new_id, utcnow
from app.jobs import queue
from app.jobs.worker import handler
from app.llm import ToolCall, ToolDefinition
from app.schemas.agents import AgentDefinition
from app.schemas.run import TERMINAL_RUN_STATUSES, RunStatus, RunStep

from . import callbacks, limits, monitoring, reviews
from .adapters import Binding, ProductionTools, is_mutating
from .deployments import CARRY_ON, LIVE, bindings_of, pause_if_needed, siblings
from .executor import ExecutionOutcome, RunCancelled, execute
from .inputs import input_problems
from .instructions import run_instructions

JOB_KIND = "runtime_execution"
SUBMIT_JOB_KIND = "runtime_submit"
PAUSED_RETRY_SECONDS = 30


class SubmitError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Caller:
    """Who started the execution (US39.4): a runtime client through the
    API (`trigger="api"`), or an internal trigger through the job queue,
    such as a scheduled sweep (no client, `trigger` names it)."""

    client_id: str | None
    label: str
    trigger: str = "api"


def refusal(deployment: AgentDeploymentModel) -> str | None:
    """Why this deployment takes no new work, if it doesn't. A deployment
    paused with `when_paused="queue"` takes it and holds it."""
    if deployment.status in ("active", "shadow"):
        return None
    if deployment.status == "paused":
        return None if deployment.when_paused == "queue" else f"This agent is paused: {deployment.pause_reason}"
    if deployment.status == "killed":
        return f"This agent was stopped: {deployment.stop_reason}"
    if deployment.status == "pending":
        return "This deployment hasn't been approved and activated yet"
    return f"This deployment is {deployment.status}"


def _enqueue(session: Session, execution_id: str, *, tag: str = "", delay_seconds: float = 0,
             extra: dict[str, Any] | None = None) -> None:
    queue.enqueue(session, JOB_KIND, {"execution_id": execution_id, **(extra or {})}, max_attempts=3,
                  delay_seconds=delay_seconds, dedupe_key=f"runtime:{execution_id}{tag}")


def _bucket(group_key: str, idempotency_key: str) -> int:
    return int(hashlib.sha256(f"{group_key}:{idempotency_key}".encode()).hexdigest()[:8], 16) % 100


def route(session: Session, entry: AgentDeploymentModel, idempotency_key: str) -> AgentDeploymentModel:
    """US39.13: the version of this agent that takes the case. A canary
    takes its share of key hashes, so the same case always lands on the
    same version. With nothing live, the deployment called answers (a
    first version in shadow takes the case itself)."""
    live = ([entry] if entry.status in LIVE else []) + siblings(session, entry, LIVE)
    canary, stable = _split(live)
    if canary is not None and stable is not None:
        return canary if _bucket(entry.group_key, idempotency_key) < canary.traffic_share else stable
    return entry if entry in live or not live else live[0]


def _split(live: list[AgentDeploymentModel]) -> tuple[AgentDeploymentModel | None, AgentDeploymentModel | None]:
    """The canary (a share of cases) and the stable version (the rest)."""
    return (next((d for d in live if d.traffic_share), None), next((d for d in live if not d.traffic_share), None))


def _new_execution(deployment: AgentDeploymentModel, caller: Caller, data: Any, key: str, **extra: Any) -> AgentExecutionModel:
    return AgentExecutionModel(
        id=new_id("exe"), deployment_id=deployment.id, process_id=deployment.process_id,
        artifact_version=deployment.artifact_version, identity_id=deployment.identity_id,
        client_id=caller.client_id, trigger=caller.trigger, idempotency_key=key, status="QUEUED", input=data,
        deviations=[], attempt=0, step_count=0, total_tokens=0, turns_used=0, **extra,
    )


def _shadow_copies(session: Session, primary: AgentExecutionModel, served_by: AgentDeploymentModel,
                   caller: Caller) -> None:
    """A copy of the case for each version in shadow. A shadow version at a
    cap simply misses the case: the live one still takes it."""
    for shadow in siblings(session, served_by, ("shadow",)):
        try:
            limits.check_caps(session, shadow)
        except limits.LimitExceeded:
            continue
        copy = _new_execution(shadow, Caller(client_id=caller.client_id, label=caller.label, trigger="shadow"),
                              primary.input, primary.idempotency_key, shadow_of=primary.id)
        session.add(copy)
        session.flush()
        _enqueue(session, copy.id)


def submit(session: Session, deployment: AgentDeploymentModel, caller: Caller, *, data: Any,
           idempotency_key: str, callback_url: str | None,
           requested_by: str | None = None) -> tuple[AgentExecutionModel, bool]:
    """Queues one case on the version that serves it (`route`). The same
    idempotency key returns the same execution (created=False); reused
    with different input, it's refused."""
    existing = _same_case(session, deployment, idempotency_key, data)
    if existing is not None:
        return existing, False
    entry = deployment
    deployment = route(session, entry, idempotency_key)
    _check_admissible(session, deployment, data, callback_url)
    execution = _new_execution(deployment, caller, data, idempotency_key, callback_url=callback_url,
                               requested_by=(requested_by or "").strip() or None)
    session.add(execution)
    session.flush()
    _enqueue(session, execution.id)
    if deployment.status != "shadow":
        _shadow_copies(session, execution, deployment, caller)
    audit.record(
        session, actor=None, actor_type="service", actor_name=caller.label, action="runtime_execution_submitted",
        object_type="agent_execution", object_id=execution.id, process_id=deployment.process_id,
        detail={"deployment_id": deployment.id, "client_id": caller.client_id, "trigger": caller.trigger,
                "identity_id": deployment.identity_id, "requested_by": execution.requested_by,
                **({"called": entry.id} if entry.id != deployment.id else {})},
    )
    return execution, True


def _same_case(session: Session, deployment: AgentDeploymentModel, key: str, data: Any) -> AgentExecutionModel | None:
    if not key or len(key) > 200:
        raise SubmitError(400, "An Idempotency-Key header (up to 200 characters) is required")
    versions = [deployment.id, *(d.id for d in siblings(session, deployment, (*CARRY_ON, "killed", "pending")))]
    existing = session.scalar(select(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id.in_(versions), AgentExecutionModel.idempotency_key == key,
        AgentExecutionModel.shadow_of.is_(None)).order_by(AgentExecutionModel.created_at).limit(1))
    if existing is not None and existing.input != data:
        raise SubmitError(409, "This Idempotency-Key was already used with a different input")
    return existing


def _check_admissible(session: Session, deployment: AgentDeploymentModel, data: Any, callback_url: str | None) -> None:
    """Running, under its caps (US39.9), with valid input and callback."""
    reason = refusal(deployment)
    if reason:
        raise SubmitError(503, reason)
    try:
        limits.check_caps(session, deployment)
    except limits.LimitExceeded as exc:
        raise SubmitError(429, f"This agent is at a limit: {exc}") from exc
    problems = input_problems(AgentDefinition.model_validate(deployment.snapshot["definition"]), data)
    if problems:
        raise SubmitError(422, "; ".join(problems))
    if callback_url and not callbacks.allowed(callback_url):
        raise SubmitError(422, "That callback host isn't allowed (RUNTIME_CALLBACK_HOSTS)")


def queue_resume(session: Session, execution: AgentExecutionModel, *, task_id: str, decision: str, note: str) -> None:
    """US39.10: the inbox's decision goes to the worker, which resumes the case."""
    _enqueue(session, execution.id, tag=f":resume:{task_id}", extra={"decision": decision, "note": note})


# ---- the handler ----------------------------------------------------------------------


def _bindings(session: Session, deployment: AgentDeploymentModel, definition: AgentDefinition) -> dict[str, Binding]:
    rows = bindings_of(session, deployment.id)
    schemas = deployment.snapshot["tool_schemas"]
    out = {}
    for contract in definition.tool_contracts:
        row = rows.get(contract.system_name)
        if row is None:
            continue
        app = session.get(ContextEntityModel, row.application_id) if row.application_id else None
        endpoints = (app.data or {}).get("endpoints") or [] if app else []
        endpoint = next((e for e in endpoints if e.get("url") == row.base_url), None)
        out[contract.system_name] = Binding(
            system_name=contract.system_name, binding_id=row.id, base_url=row.base_url,
            environment=endpoint.get("environment") if endpoint else None, operations=list(row.operations),
            api_operations=schemas[contract.system_name]["api_operations"], credential=dict(row.credential),
            contract=contract, timeout_seconds=contract.timeout_seconds,
        )
    return out


def _sent_mutating_call(session: Session, execution: AgentExecutionModel, definition: AgentDefinition) -> bool:
    """Did the last attempt let a data-changing call through the gateway?
    After the policy check it may have been sent, so its outcome is unknown."""
    mutating = {c.system_name for c in definition.tool_contracts if is_mutating(c)}
    steps = session.scalars(select(ExecutionStepModel).where(
        ExecutionStepModel.execution_id == execution.id, ExecutionStepModel.attempt == execution.attempt,
        ExecutionStepModel.type == "POLICY_CHECK"))
    return any(s.target in mutating and not s.step.get("denied") for s in steps)


def _finish(execution: AgentExecutionModel, status: RunStatus, reason: str | None = None) -> None:
    execution.status = status
    execution.completed_at = utcnow()
    if reason:
        execution.deviations = [*execution.deviations, {"reason": reason, "step_index": None}]


class _AwaitReview:
    """Production's HumanPort: nobody decides on the spot, so a checkpoint
    waits for a person (the review inbox)."""

    async def decide(self, proposed_action: dict[str, Any]) -> None:
        return None


def in_shadow(execution: AgentExecutionModel, deployment: AgentDeploymentModel) -> bool:
    """A copy of a live case, or a case of a version that only shadows."""
    return execution.shadow_of is not None or deployment.status == "shadow"


class _ShadowApproves:
    """In shadow nothing that changes data is sent, so no one needs asking:
    the checkpoint is approved and the case shows what it would do."""

    async def decide(self, proposed_action: dict[str, Any]) -> str:
        return "approve"


@dataclass
class _Circuit:
    session: Session
    deployment: AgentDeploymentModel

    def blocks(self, system_name: str) -> str | None:
        return limits.circuit_blocks(self.session, self.deployment, system_name)

    def record(self, system_name: str, *, failed: bool) -> None:
        limits.record_call(self.session, self.deployment, system_name, failed=failed)
        self.session.commit()


def _held_while_paused(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel) -> bool:
    """A paused deployment that queues: the case waits and is retried."""
    if deployment.status != "paused" or execution.attempt > 0:
        return False
    _enqueue(session, execution.id, tag=f":held:{utcnow().timestamp():.0f}", delay_seconds=PAUSED_RETRY_SECONDS)
    return True


def _ended_before_starting(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel,
                           definition: AgentDefinition, *, resuming: bool) -> bool:
    """Ends the execution without running it when its deployment stopped,
    or when a dead attempt may already have changed data."""
    # A retired version finishes what it accepted; killed or never
    # activated, it runs nothing.
    reason = refusal(deployment) if deployment.status not in ("paused", "retired") else None
    if reason:
        _finish(execution, "KILLED", f"Stopped before it started: {reason}")
        return True
    if not resuming and execution.attempt > 0 and not execution.shadow_of and _sent_mutating_call(
            session, execution, definition):
        _finish(execution, "ESCALATED", "The worker stopped after a call that changes data was sent; its outcome "
                                         "is unknown, so the case needs a person rather than a retry")
        reviews.open_escalation(session, execution, deployment)
        return True
    return False


def _record_outcome(execution: AgentExecutionModel, outcome: ExecutionOutcome, *, resumed: bool) -> None:
    """A resumed leg's tokens and cost add to the first leg's."""
    execution.final_output = outcome.final_output
    deviations = [d.model_dump(mode="json") for d in outcome.policy_deviations]
    if outcome.terminal_deviation is not None:
        deviations.append(outcome.terminal_deviation.model_dump(mode="json"))
    execution.deviations = (list(execution.deviations or []) if resumed else []) + deviations
    _add_usage(execution, outcome, resumed=resumed)
    execution.turns_used = outcome.turns_used
    execution.resume_state = outcome.resume_state
    execution.status = outcome.status
    if outcome.status in TERMINAL_RUN_STATUSES:
        execution.completed_at = utcnow()


def _add_usage(execution: AgentExecutionModel, outcome: ExecutionOutcome, *, resumed: bool) -> None:
    """Unknown cost on either leg makes the total unknown, as in the twin."""
    if not resumed:
        execution.total_tokens, execution.total_cost_usd = outcome.total_tokens, outcome.total_cost_usd
        return
    execution.total_tokens += outcome.total_tokens
    if execution.total_cost_usd is None or outcome.total_cost_usd is None:
        execution.total_cost_usd = None
    else:
        execution.total_cost_usd += outcome.total_cost_usd


def _after(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel) -> None:
    """Opens the inbox task the outcome calls for, checks the pause rules,
    alerts and drift, and exports the trace (US39.14-39.15). A shadow case
    involves no one."""
    if not in_shadow(execution, deployment):
        if execution.status == "WAITING_FOR_HUMAN":
            reviews.open_checkpoint(session, execution, deployment)
        elif execution.status == "ESCALATED":
            reviews.open_escalation(session, execution, deployment)
    if execution.status in TERMINAL_RUN_STATUSES:
        pause_if_needed(session, deployment)
        callbacks.notify_caller(session, execution)
        monitoring.after_case(session, execution, deployment)


@dataclass
class _Recorder:
    """The executor's callbacks for one execution: steps appended and
    committed one by one, the kill switch before every model and tool call
    (US39.11), and the audit event for every data-changing call."""

    session: Session
    execution: AgentExecutionModel
    deployment: AgentDeploymentModel
    identity: AgentIdentityModel

    async def on_step(self, step: RunStep) -> None:
        self.session.add(ExecutionStepModel(
            id=new_id("xstep"), execution_id=self.execution.id, attempt=self.execution.attempt, seq=step.seq,
            type=step.type, target=step.target, step=step.model_dump(mode="json"), occurred_at=step.occurred_at,
        ))
        self.execution.step_count += 1
        self.session.commit()

    async def on_phase(self, status: RunStatus) -> None:
        current = self.session.execute(
            select(AgentDeploymentModel.status, AgentDeploymentModel.stop_reason, AgentIdentityModel.status)
            .join(AgentIdentityModel, AgentIdentityModel.id == AgentDeploymentModel.identity_id)
            .where(AgentDeploymentModel.id == self.deployment.id)
        ).one()
        if current[0] not in CARRY_ON or current[2] != "active":
            raise RunCancelled("KILLED", f"Stopped by the kill switch: {current[1] or 'the identity was revoked'}")
        self.execution.status = status
        self.session.commit()

    async def on_call(self, binding: Binding, call: ToolCall, status_code: int) -> None:
        if not is_mutating(binding.contract):
            return
        audit.record(
            self.session, actor=None, actor_type="agent", actor_name=self.identity.display_name,
            action="agent_tool_call", object_type="agent_execution", object_id=self.execution.id,
            process_id=self.execution.process_id,
            detail={"identity_id": self.identity.id,
                    "on_behalf_of": self.execution.client_id or f"trigger:{self.execution.trigger}",
                    "system": binding.system_name, "operation": call.arguments.get("operation"),
                    "status_code": status_code},
        )


def _tools(deployment: AgentDeploymentModel) -> dict[str, ToolDefinition]:
    return {
        name: ToolDefinition(name=s["tool_name"], description=s["description"], parameters=s["parameters"])
        for name, s in deployment.snapshot["tool_schemas"].items()
    }


def _start_leg(session: Session, execution: AgentExecutionModel, *, resuming: bool) -> None:
    """A new attempt, or the resumed leg of the current one."""
    if not resuming:
        execution.attempt += 1
    execution.status = "RUNNING"
    execution.started_at = execution.started_at or utcnow()
    session.commit()


def _admit(session: Session, execution: AgentExecutionModel | None, payload: dict[str, Any]):
    """The deployment, definition and the decision being resumed with (None
    for a fresh attempt) -- or the job's result when it runs nothing."""
    if execution is None or execution.status in TERMINAL_RUN_STATUSES:
        return {"skipped": True}
    deployment = session.get(AgentDeploymentModel, execution.deployment_id)
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    waiting = execution.status == "WAITING_FOR_HUMAN"
    decision = payload.get("decision") if waiting else None
    if waiting and not decision:
        return {"skipped": True}  # waiting for its review
    if _ended_before_starting(session, execution, deployment, definition, resuming=bool(decision)) or (
            not decision and _held_while_paused(session, execution, deployment)):
        if execution.status in TERMINAL_RUN_STATUSES:
            callbacks.notify_caller(session, execution)
        session.commit()
        return {"status": execution.status}
    return deployment, definition, decision


@handler(JOB_KIND)
async def runtime_execution(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from app.connectors.jobs import get_job_llm

    execution = session.get(AgentExecutionModel, payload["execution_id"])
    admitted = _admit(session, execution, payload)
    if isinstance(admitted, dict):
        return admitted
    deployment, definition, decision = admitted
    resume_state = execution.resume_state if decision else None
    _start_leg(session, execution, resuming=bool(decision))
    recorder = _Recorder(session, execution, deployment, session.get(AgentIdentityModel, execution.identity_id))
    output_fields = [f.name for f in definition.output_schema]
    outcome = await execute(
        get_job_llm(),
        artifact=definition,
        tools=_tools(deployment),
        tool_port=ProductionTools(_bindings(session, deployment, definition), execution_id=execution.id,
                                  on_call=recorder.on_call, circuit=_Circuit(session, deployment),
                                  shadow=in_shadow(execution, deployment)),
        human_port=_ShadowApproves() if in_shadow(execution, deployment) else _AwaitReview(),
        inputs=execution.input or {},
        instructions=run_instructions(has_checkpoint=definition.human_checkpoint != "none",
                                      output_fields=output_fields, test_run=False),
        output_fields=output_fields,
        max_turns=get_settings().runtime_max_turns,
        operation="runtime_execution",
        resume_state=resume_state,
        human_decision=decision,
        human_note=payload.get("note") if decision else None,
        on_step=recorder.on_step,
        on_phase=recorder.on_phase,
    )
    _record_outcome(execution, outcome, resumed=bool(decision))
    _after(session, execution, deployment)
    session.commit()
    return {"status": execution.status}


# ---- internal triggers ------------------------------------------------------------------


def queue_submission(session: Session, deployment_id: str, *, data: dict[str, Any], idempotency_key: str,
                     trigger: str, requested_by: str | None = None) -> None:
    """US39.4: starts a case from inside the platform (a scheduled sweep,
    another job) the way a runtime client would through the API: same
    checks, same idempotency. A cap that is reached is retried with
    backoff; any other refusal is recorded in the audit log and the job's
    result."""
    queue.enqueue(session, SUBMIT_JOB_KIND,
                  {"deployment_id": deployment_id, "input": data, "idempotency_key": idempotency_key,
                   "trigger": trigger, "requested_by": requested_by},
                  max_attempts=5, dedupe_key=f"runtime-submit:{deployment_id}:{idempotency_key}")


@handler(SUBMIT_JOB_KIND)
async def runtime_submit(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    trigger = payload["trigger"]
    deployment = session.get(AgentDeploymentModel, payload["deployment_id"])
    try:
        if deployment is None:
            raise SubmitError(404, f"Deployment '{payload['deployment_id']}' not found")
        execution, created = submit(session, deployment, Caller(client_id=None, label=f"trigger:{trigger}",
                                                                trigger=trigger),
                                    data=payload["input"], idempotency_key=payload["idempotency_key"],
                                    callback_url=None, requested_by=payload.get("requested_by"))
    except SubmitError as exc:
        if exc.status == 429:
            raise  # a cap: try again later
        session.rollback()
        audit.record(
            session, actor=None, actor_type="service", actor_name=f"trigger:{trigger}",
            action="runtime_execution_refused", object_type="agent_deployment", object_id=payload["deployment_id"],
            process_id=deployment.process_id if deployment else None,
            detail={"trigger": trigger, "status": exc.status, "reason": exc.detail,
                    "idempotency_key": payload["idempotency_key"]},
            outcome="refused",
        )
        return {"refused": exc.status, "reason": exc.detail}
    return {"execution_id": execution.id, "created": created}


def steps_of(session: Session, execution_id: str) -> list[ExecutionStepModel]:
    return list(session.scalars(select(ExecutionStepModel).where(ExecutionStepModel.execution_id == execution_id)
                                .order_by(ExecutionStepModel.attempt, ExecutionStepModel.seq)))


def counts_by_status(session: Session, deployment_id: str) -> dict[str, int]:
    rows = session.execute(select(AgentExecutionModel.status, func.count()).where(
        AgentExecutionModel.deployment_id == deployment_id).group_by(AgentExecutionModel.status))
    return {status: n for status, n in rows}
