"""Epic 39, US39.9 and US39.12: caps across executions, the per-tool
circuit breaker, and the rules that pause a deployment on their own.

**Caps** (per deployment) are checked when a case arrives: executions per
minute, executions running at once, spend today, and data-changing calls
in the last hour. Over a cap, the case is refused with 429 and the
reason; running cases finish. The tenant's own limits (Epic 38, US38.9)
apply on top. Defaults come from the twin's evidence: its cost per case
and how many calls that change data a case makes, and the as-is
operating model's monthly volume (Epic 36), with headroom. `basis` says
what each default was computed from.

**Circuit breaker** (per deployment and system, shared by every worker):
opened after `failures_to_open` failures in a row, or when more than
`max_error_rate` of the last `window` calls failed (with at least
`min_calls`). A failure is a transport error or a 5xx; a 4xx is the
system answering. While open, a case that needs the system escalates
instead of calling it. After `cooldown_seconds` the next call goes
through as the probe: success closes the circuit, failure reopens it.

**Pause rules** are checked after each case ends and each review
decision: the share of recent cases with a denied tool call, the share
that failed, the last hour's spend against the twin's cost per case,
and the share of reviews rejected. Any one over its threshold pauses
the deployment and tells its owners why, with the numbers.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentDeploymentModel,
    AgentExecutionModel,
    ExecutionStepModel,
    ProcessOperatingModel,
    ReviewTaskModel,
    SimulationRunModel,
    ToolCircuitModel,
)
from app.ids import utcnow
from app.schemas.agents import AgentDefinition
from app.schemas.run import NON_TERMINAL_RUN_STATUSES, TERMINAL_RUN_STATUSES

from .adapters import is_mutating

WORKING_DAYS_PER_MONTH = 22
WORKING_MINUTES_PER_DAY = 480
HEADROOM = 3
CIRCUIT_DEFAULTS = {"failures_to_open": 5, "window": 20, "min_calls": 10, "max_error_rate": 0.5, "cooldown_seconds": 60}
POLICY_DENIAL_PREFIX = "Tool call to '"


class LimitExceeded(Exception):
    pass


def _now() -> datetime:
    return utcnow().replace(tzinfo=None)


# ---- defaults from the twin -------------------------------------------------------------


def twin_evidence(session: Session, artifact_id: str, definition: AgentDefinition) -> dict[str, Any]:
    """The twin's cost per case and data-changing calls per case for this
    artifact, from its completed runs."""
    runs = list(session.scalars(select(SimulationRunModel).where(
        SimulationRunModel.agent_artifact_id == artifact_id, SimulationRunModel.status == "COMPLETED")))
    mutating = {c.system_name for c in definition.tool_contracts if is_mutating(c)}
    costs = [r.total_cost_usd for r in runs if r.total_cost_usd is not None]
    writes = [_writes(r.steps, mutating) for r in runs if r.steps]
    return {"runs": len(runs), "cost_per_case_usd": _mean(costs, 6), "writes_per_case": _mean(writes, 3)}


def _writes(steps: list[dict[str, Any]], mutating: set[str]) -> int:
    return sum(1 for s in steps if s.get("type") == "TOOL_EXECUTED" and s.get("target") in mutating)


def _mean(values: list[float], digits: int) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def default_limits(session: Session, process_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    operating = session.get(ProcessOperatingModel, process_id)
    monthly = (operating.model or {}).get("monthly_volume") if operating is not None else None
    per_day = monthly / WORKING_DAYS_PER_MONTH if monthly else None
    basis = []
    if per_day:
        per_minute = max(10, math.ceil(per_day / WORKING_MINUTES_PER_DAY * 10 * HEADROOM))
        basis.append(f"{monthly} cases a month in the operating model, about {per_day:.0f} a working day")
    else:
        per_minute = 10
        basis.append("no operating model volume, so standard caps")
    cost = evidence.get("cost_per_case_usd")
    if cost and per_day:
        spend = max(1.0, round(cost * per_day * HEADROOM, 2))
        basis.append(f"the twin's ${cost:.4f} a case, {HEADROOM}x headroom")
    else:
        spend = 25.0
        basis.append("no twin cost per case, so a $25 daily cap" if not cost else "a $25 daily cap without volume")
    writes = evidence.get("writes_per_case")
    if writes and per_day:
        per_hour = max(20, math.ceil(per_day / (WORKING_MINUTES_PER_DAY / 60) * writes * HEADROOM))
        basis.append(f"{writes:g} data-changing calls a case in the twin")
    else:
        per_hour = 100
    return {
        "executions_per_minute": per_minute,
        "max_concurrent": 5,
        "spend_per_day_usd": spend,
        "mutating_calls_per_hour": per_hour,
        "basis": "; ".join(basis),
    }


def default_pause_rules(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_denials": {"enabled": True, "max_rate": 0.2, "window": 20, "min_cases": 10},
        "errors": {"enabled": True, "max_rate": 0.3, "window": 20, "min_cases": 10},
        "spend": {"enabled": evidence.get("cost_per_case_usd") is not None, "multiple": 3.0,
                  "baseline_cost_per_case_usd": evidence.get("cost_per_case_usd")},
        "rejections": {"enabled": True, "max_rate": 0.5, "window": 20, "min_decisions": 5},
        "circuit": dict(CIRCUIT_DEFAULTS),
    }


# ---- caps, when a case arrives -----------------------------------------------------------


RUNNING_STATUSES = NON_TERMINAL_RUN_STATUSES - {"QUEUED", "WAITING_FOR_HUMAN", "CREATED"}


def _usage(session: Session, deployment: AgentDeploymentModel, cap: str) -> tuple[float, str]:
    """How much of `cap` is used now, and how to say it."""
    now = _now()
    executions = select(func.count()).select_from(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment.id)
    if cap == "executions_per_minute":
        n = session.scalar(executions.where(AgentExecutionModel.created_at >= now - timedelta(minutes=1)))
        return n, f"{n} cases in the last minute"
    if cap == "max_concurrent":
        n = session.scalar(executions.where(AgentExecutionModel.status.in_(RUNNING_STATUSES)))
        return n, f"{n} cases running"
    if cap == "spend_per_day_usd":
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = session.scalar(select(func.coalesce(func.sum(AgentExecutionModel.total_cost_usd), 0.0)).where(
            AgentExecutionModel.deployment_id == deployment.id, AgentExecutionModel.created_at >= midnight)) or 0.0
        return spent, f"${spent:.2f} spent today"
    n = mutating_calls_since(session, deployment, now - timedelta(hours=1))
    return n, f"{n} data-changing calls in the last hour"


def check_caps(session: Session, deployment: AgentDeploymentModel) -> None:
    for cap in ("executions_per_minute", "max_concurrent", "spend_per_day_usd", "mutating_calls_per_hour"):
        limit = (deployment.limits or {}).get(cap)
        if not limit:
            continue
        used, said = _usage(session, deployment, cap)
        if used >= limit:
            raise LimitExceeded(f"{said}; the cap is {limit}")


def mutating_calls_since(session: Session, deployment: AgentDeploymentModel, since: datetime) -> int:
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    mutating = [c.system_name for c in definition.tool_contracts if is_mutating(c)]
    if not mutating:
        return 0
    return session.scalar(
        select(func.count()).select_from(ExecutionStepModel)
        .join(AgentExecutionModel, AgentExecutionModel.id == ExecutionStepModel.execution_id)
        .where(AgentExecutionModel.deployment_id == deployment.id, ExecutionStepModel.type == "TOOL_EXECUTED",
               ExecutionStepModel.target.in_(mutating), ExecutionStepModel.occurred_at >= since)
    ) or 0


# ---- the circuit breaker -------------------------------------------------------------------


def _circuit_settings(deployment: AgentDeploymentModel) -> dict[str, Any]:
    return {**CIRCUIT_DEFAULTS, **((deployment.pause_rules or {}).get("circuit") or {})}


def _circuit(session: Session, deployment_id: str, system_name: str) -> ToolCircuitModel:
    row = session.get(ToolCircuitModel, (deployment_id, system_name))
    if row is None:
        row = ToolCircuitModel(deployment_id=deployment_id, system_name=system_name, state="closed",
                               consecutive_failures=0, recent=[])
        session.add(row)
        session.flush()
    return row


def circuit_blocks(session: Session, deployment: AgentDeploymentModel, system_name: str) -> str | None:
    """Why `system_name` mustn't be called now, if it mustn't. An open
    circuit past its cool-down lets the call through as the probe."""
    row = session.get(ToolCircuitModel, (deployment.id, system_name))
    if row is None or row.state != "open":
        return None
    cooldown = timedelta(seconds=_circuit_settings(deployment)["cooldown_seconds"])
    if row.opened_at is not None and _now() - row.opened_at >= cooldown:
        return None
    return f"'{system_name}' is failing ({row.reason}), so its circuit is open; a person should take this case"


def record_call(session: Session, deployment: AgentDeploymentModel, system_name: str, *, failed: bool) -> None:
    settings = _circuit_settings(deployment)
    row = _circuit(session, deployment.id, system_name)
    row.recent = (list(row.recent or []) + [1 if failed else 0])[-settings["window"]:]
    row.consecutive_failures = row.consecutive_failures + 1 if failed else 0
    rate = sum(row.recent) / len(row.recent)
    if not failed:
        row.state, row.opened_at, row.reason = "closed", None, None
    elif row.state == "open" or row.consecutive_failures >= settings["failures_to_open"]:
        # Reopened by a failed probe, or opened by failures in a row.
        row.state, row.opened_at = "open", _now()
        row.reason = f"{row.consecutive_failures} failures in a row"
    elif len(row.recent) >= settings["min_calls"] and rate > settings["max_error_rate"]:
        row.state, row.opened_at = "open", _now()
        row.reason = f"{sum(row.recent)} of the last {len(row.recent)} calls failed"
    session.flush()


def circuits_of(session: Session, deployment_id: str) -> list[ToolCircuitModel]:
    return list(session.scalars(select(ToolCircuitModel).where(ToolCircuitModel.deployment_id == deployment_id)))


# ---- pause rules --------------------------------------------------------------------------


def _recent_finished(session: Session, deployment_id: str, window: int) -> list[AgentExecutionModel]:
    return list(session.scalars(
        select(AgentExecutionModel).where(AgentExecutionModel.deployment_id == deployment_id,
                                          AgentExecutionModel.status.in_(TERMINAL_RUN_STATUSES - {"KILLED"}))
        .order_by(AgentExecutionModel.completed_at.desc()).limit(window)))


def _rate_rule(rule: dict[str, Any], cases: list[AgentExecutionModel], hit, label: str) -> str | None:
    if not rule.get("enabled") or len(cases) < rule.get("min_cases", 10):
        return None
    hits = sum(1 for c in cases if hit(c))
    if hits / len(cases) > rule["max_rate"]:
        return f"{hits} of the last {len(cases)} cases {label} (the limit is {rule['max_rate']:.0%})"
    return None


def _spend_rule(session: Session, deployment: AgentDeploymentModel, rule: dict[str, Any]) -> str | None:
    baseline = rule.get("baseline_cost_per_case_usd")
    if not rule.get("enabled") or not baseline:
        return None
    since = _now() - timedelta(hours=1)
    count, spent = session.execute(select(func.count(), func.coalesce(func.sum(AgentExecutionModel.total_cost_usd), 0.0))
                                   .where(AgentExecutionModel.deployment_id == deployment.id,
                                          AgentExecutionModel.created_at >= since)).one()
    expected = baseline * count * rule["multiple"]
    if count and spent > expected:
        return (f"${spent:.4f} spent on {count} cases in the last hour, over {rule['multiple']:g}x the twin's "
                f"${baseline:.4f} a case")
    return None


def _rejection_rule(session: Session, deployment: AgentDeploymentModel, rule: dict[str, Any]) -> str | None:
    if not rule.get("enabled"):
        return None
    decided = list(session.scalars(
        select(ReviewTaskModel.decision).where(ReviewTaskModel.deployment_id == deployment.id,
                                               ReviewTaskModel.status == "decided")
        .order_by(ReviewTaskModel.decided_at.desc()).limit(rule["window"])))
    if len(decided) < rule.get("min_decisions", 5):
        return None
    rejected = decided.count("reject")
    if rejected / len(decided) > rule["max_rate"]:
        return f"reviewers rejected {rejected} of the last {len(decided)} checkpoints (the limit is {rule['max_rate']:.0%})"
    return None


def pause_trigger(session: Session, deployment: AgentDeploymentModel) -> str | None:
    """The first pause rule this deployment now breaks, with its numbers."""
    rules = deployment.pause_rules or {}
    denials, errors = rules.get("policy_denials") or {}, rules.get("errors") or {}
    cases = _recent_finished(session, deployment.id, max(denials.get("window", 20), errors.get("window", 20)))
    return (
        _rate_rule(denials, cases[: denials.get("window", 20)], _had_denial, "had a tool call denied by policy")
        or _rate_rule(errors, cases[: errors.get("window", 20)], _failed, "failed")
        or _spend_rule(session, deployment, rules.get("spend") or {})
        or _rejection_rule(session, deployment, rules.get("rejections") or {})
    )


def _had_denial(case: AgentExecutionModel) -> bool:
    return any((d.get("reason") or "").startswith(POLICY_DENIAL_PREFIX) for d in case.deviations or [])


def _failed(case: AgentExecutionModel) -> bool:
    return case.status in ("FAILED", "TIMED_OUT")
