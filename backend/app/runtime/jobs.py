"""Epic 39, US39.3/US39.4/US39.8/US39.11: running one execution.

`submit` takes a case from a tenant's system and queues it as a
`runtime_execution` job. The handler runs it through the shared executor
with production ports:

- tools: app/runtime/adapters.py, bound per deployment;
- humans: no one decides on the spot, so a checkpoint suspends the
  execution as WAITING_FOR_HUMAN (the review inbox is US39.10);
- steps: appended to `execution_steps` and committed one by one, so a
  crash loses none that happened.

Before every model call and tool call (`on_phase`), the handler reads the
deployment and identity status from the database. If the deployment was
killed, retired or its identity revoked, the run stops right there as
KILLED. A tool call already sent is allowed to finish; nothing runs
after it.

Restarts: a job whose worker died is claimed again after its lease. If
the dead attempt had already sent a call that changes data, its outcome
is unknown, so the execution ends ESCALATED rather than risk sending it
twice. Otherwise the case starts again as a new attempt.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
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

from .adapters import Binding, ProductionTools, is_mutating
from .deployments import bindings_of
from .executor import ExecutionOutcome, RunCancelled, execute
from .instructions import run_instructions

logger = logging.getLogger("app.runtime")
JOB_KIND = "runtime_execution"


class SubmitError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Caller:
    """Who started the execution: a runtime client (US39.4)."""

    client_id: str
    label: str


def _input_problems(definition: AgentDefinition, data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["the input must be a JSON object"]
    missing = [f.name for f in definition.input_schema if f.name not in data]
    return [f"missing input field '{name}'" for name in missing]


def _callback_allowed(url: str) -> bool:
    parsed = urlparse(url)
    patterns = [p.strip() for p in get_settings().runtime_callback_hosts.split(",") if p.strip()]
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and any(fnmatch.fnmatch(host, p) for p in patterns)


def refusal(deployment: AgentDeploymentModel) -> str | None:
    """Why this deployment takes no new work, if it doesn't."""
    if deployment.status == "active":
        return None
    if deployment.status == "killed":
        return f"This agent was stopped: {deployment.stop_reason}"
    if deployment.status == "pending":
        return "This deployment hasn't been approved and activated yet"
    return f"This deployment is {deployment.status}"


def submit(session: Session, deployment: AgentDeploymentModel, caller: Caller, *, data: Any,
           idempotency_key: str, callback_url: str | None) -> tuple[AgentExecutionModel, bool]:
    """Queues one case. The same idempotency key returns the same
    execution (created=False); reused with different input, it's refused."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise SubmitError(400, "An Idempotency-Key header (up to 200 characters) is required")
    existing = session.scalar(select(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment.id, AgentExecutionModel.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.input != data:
            raise SubmitError(409, "This Idempotency-Key was already used with a different input")
        return existing, False
    reason = refusal(deployment)
    if reason:
        raise SubmitError(503, reason)
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    problems = _input_problems(definition, data)
    if problems:
        raise SubmitError(422, "; ".join(problems))
    if callback_url and not _callback_allowed(callback_url):
        raise SubmitError(422, "That callback host isn't allowed (RUNTIME_CALLBACK_HOSTS)")
    execution = AgentExecutionModel(
        id=new_id("exe"), deployment_id=deployment.id, process_id=deployment.process_id,
        artifact_version=deployment.artifact_version, identity_id=deployment.identity_id,
        client_id=caller.client_id, idempotency_key=idempotency_key, status="QUEUED", input=data,
        callback_url=callback_url, deviations=[], attempt=0, step_count=0, total_tokens=0, turns_used=0,
    )
    session.add(execution)
    session.flush()
    queue.enqueue(session, JOB_KIND, {"execution_id": execution.id}, max_attempts=3,
                  dedupe_key=f"runtime:{execution.id}")
    audit.record(
        session, actor=None, actor_type="service", actor_name=caller.label, action="runtime_execution_submitted",
        object_type="agent_execution", object_id=execution.id, process_id=deployment.process_id,
        detail={"deployment_id": deployment.id, "client_id": caller.client_id, "identity_id": deployment.identity_id},
    )
    return execution, True


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


async def _callback(execution: AgentExecutionModel) -> None:
    if not execution.callback_url or not _callback_allowed(execution.callback_url):
        return
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            await client.post(execution.callback_url, json={"execution_id": execution.id, "status": execution.status})
    except httpx.HTTPError as exc:
        logger.warning("runtime_callback_failed", extra={"execution_id": execution.id, "error": type(exc).__name__})


@handler(JOB_KIND)
async def runtime_execution(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from app.connectors.jobs import get_job_llm

    execution = session.get(AgentExecutionModel, payload["execution_id"])
    if execution is None or execution.status in TERMINAL_RUN_STATUSES:
        return {"skipped": True}
    deployment = session.get(AgentDeploymentModel, execution.deployment_id)
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    if _ended_before_starting(session, execution, deployment, definition):
        session.commit()
        return {"status": execution.status}

    execution.attempt += 1
    execution.status = "RUNNING"
    execution.started_at = execution.started_at or utcnow()
    session.commit()
    attempt = execution.attempt
    identity = session.get(AgentIdentityModel, execution.identity_id)

    async def on_step(step: RunStep) -> None:
        session.add(ExecutionStepModel(
            id=new_id("xstep"), execution_id=execution.id, attempt=attempt, seq=step.seq, type=step.type,
            target=step.target, step=step.model_dump(mode="json"), occurred_at=step.occurred_at,
        ))
        execution.step_count += 1
        session.commit()

    async def on_phase(status: RunStatus) -> None:
        # US39.11: the kill switch, checked before every model and tool call.
        current = session.execute(
            select(AgentDeploymentModel.status, AgentDeploymentModel.stop_reason, AgentIdentityModel.status)
            .join(AgentIdentityModel, AgentIdentityModel.id == AgentDeploymentModel.identity_id)
            .where(AgentDeploymentModel.id == deployment.id)
        ).one()
        if current[0] not in ("active", "paused") or current[2] != "active":
            raise RunCancelled("KILLED", f"Stopped by the kill switch: {current[1] or 'the identity was revoked'}")
        execution.status = status
        session.commit()

    async def on_call(binding: Binding, call: ToolCall, status_code: int) -> None:
        if is_mutating(binding.contract):
            audit.record(
                session, actor=None, actor_type="agent", actor_name=identity.display_name, action="agent_tool_call",
                object_type="agent_execution", object_id=execution.id, process_id=execution.process_id,
                detail={"identity_id": identity.id, "on_behalf_of": execution.client_id, "system": binding.system_name,
                        "operation": call.arguments.get("operation"), "status_code": status_code},
            )

    schemas = deployment.snapshot["tool_schemas"]
    tools = {
        name: ToolDefinition(name=s["tool_name"], description=s["description"], parameters=s["parameters"])
        for name, s in schemas.items()
    }
    output_fields = [f.name for f in definition.output_schema]
    outcome = await execute(
        get_job_llm(),
        artifact=definition,
        tools=tools,
        tool_port=ProductionTools(_bindings(session, deployment, definition), execution_id=execution.id, on_call=on_call),
        human_port=_AwaitReview(),
        inputs=execution.input or {},
        instructions=run_instructions(has_checkpoint=definition.human_checkpoint != "none",
                                      output_fields=output_fields, test_run=False),
        output_fields=output_fields,
        max_turns=get_settings().runtime_max_turns,
        operation="runtime_execution",
        on_step=on_step,
        on_phase=on_phase,
    )
    _record_outcome(execution, outcome)
    session.commit()
    if outcome.status in TERMINAL_RUN_STATUSES:
        await _callback(execution)
    return {"status": execution.status}


def _ended_before_starting(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel,
                           definition: AgentDefinition) -> bool:
    """Ends the execution without running it when its deployment stopped,
    or when a dead attempt may already have changed data."""
    reason = refusal(deployment)
    if reason:
        _finish(execution, "KILLED", f"Stopped before it started: {reason}")
        return True
    if execution.attempt > 0 and _sent_mutating_call(session, execution, definition):
        _finish(execution, "ESCALATED", "The worker stopped after a call that changes data was sent; its outcome "
                                         "is unknown, so the case needs a person rather than a retry")
        return True
    return False


def _record_outcome(execution: AgentExecutionModel, outcome: ExecutionOutcome) -> None:
    execution.final_output = outcome.final_output
    deviations = [d.model_dump(mode="json") for d in outcome.policy_deviations]
    if outcome.terminal_deviation is not None:
        deviations.append(outcome.terminal_deviation.model_dump(mode="json"))
    execution.deviations = deviations
    execution.total_tokens = outcome.total_tokens
    execution.total_cost_usd = outcome.total_cost_usd
    execution.turns_used = outcome.turns_used
    execution.resume_state = outcome.resume_state
    execution.status = outcome.status
    if outcome.status in TERMINAL_RUN_STATUSES:
        execution.completed_at = utcnow()


class _AwaitReview:
    """Production's HumanPort until the review inbox exists (US39.10): a
    checkpoint always waits for a person."""

    async def decide(self, proposed_action: dict[str, Any]) -> None:
        return None


def steps_of(session: Session, execution_id: str) -> list[ExecutionStepModel]:
    return list(session.scalars(select(ExecutionStepModel).where(ExecutionStepModel.execution_id == execution_id)
                                .order_by(ExecutionStepModel.attempt, ExecutionStepModel.seq)))


def counts_by_status(session: Session, deployment_id: str) -> dict[str, int]:
    rows = session.execute(select(AgentExecutionModel.status, func.count()).where(
        AgentExecutionModel.deployment_id == deployment_id).group_by(AgentExecutionModel.status))
    return {status: n for status, n in rows}
