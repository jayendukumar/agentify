"""Epic 39, US39.14: how each deployment is doing, over the last hour, day
or week.

Everything is computed from what the runtime already records -- the
executions, their steps, the review tasks, and the audit log for pauses
and kills -- so a number on the page can always be traced to the cases
behind it. Shadow copies count for the shadow version only.

Rates carry a Wilson 95% interval (Epic 35), so "2 of 3 failed" doesn't
read like "200 of 300 failed".
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.batches.results import _percentile, wilson
from app.db.models import AgentDeploymentModel, AgentExecutionModel, AuditEventModel, ExecutionStepModel, ReviewTaskModel
from app.ids import utcnow
from app.schemas.run import TERMINAL_RUN_STATUSES

WINDOWS = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(days=7)}
STOP_ACTIONS = ("deployment_paused", "deployment_resumed", "deployment_killed", "agent_killed", "tenant_agents_killed",
                "deployment_rolled_back")


def since(window: str) -> datetime:
    return utcnow().replace(tzinfo=None) - WINDOWS[window]


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end.replace(tzinfo=None) - start.replace(tzinfo=None)).total_seconds())


def executions_in(session: Session, deployment_id: str, start: datetime) -> list[AgentExecutionModel]:
    return list(session.scalars(select(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment_id, AgentExecutionModel.created_at >= start)))


def _tools(session: Session, execution_ids: list[str]) -> tuple[dict[str, dict[str, int]], int]:
    """Calls and errors per tool, and calls the policy denied."""
    if not execution_ids:
        return {}, 0
    rows = session.execute(
        select(ExecutionStepModel.type, ExecutionStepModel.target, ExecutionStepModel.step)
        .where(ExecutionStepModel.execution_id.in_(execution_ids),
               ExecutionStepModel.type.in_(("TOOL_EXECUTED", "TOOL_FAILED", "POLICY_CHECK"))))
    tools: dict[str, dict[str, int]] = {}
    denied = 0
    for step_type, target, step in rows:
        if step_type == "POLICY_CHECK":
            denied += bool((step or {}).get("denied"))
            continue
        entry = tools.setdefault(target or "?", {"calls": 0, "errors": 0, "not_sent": 0})
        entry["calls"] += 1
        entry["errors"] += step_type == "TOOL_FAILED"
        entry["not_sent"] += bool(((step or {}).get("result") or {}).get("shadow"))
    return dict(sorted(tools.items())), denied


def _wait_minutes(tasks: list[ReviewTaskModel]) -> dict[str, float | None]:
    waits = [_seconds(t.created_at, t.decided_at) for t in tasks if t.decided_at is not None]
    now = utcnow().replace(tzinfo=None)
    waiting = [(now - t.created_at).total_seconds() for t in tasks if t.status == "open"]
    return {
        "wait_minutes_median": round(_percentile([w for w in waits if w is not None], 0.5) / 60, 1) if waits else None,
        "waiting_now_minutes_max": round(max(waiting, default=0) / 60, 1),
    }


def _reviews(session: Session, deployment_id: str, start: datetime) -> dict[str, Any]:
    tasks = list(session.scalars(select(ReviewTaskModel).where(
        ReviewTaskModel.deployment_id == deployment_id, ReviewTaskModel.created_at >= start)))
    decisions = Counter(t.decision for t in tasks if t.decision in ("approve", "reject"))
    return {
        "opened": len(tasks),
        "open": sum(1 for t in tasks if t.status == "open"),
        "approved": decisions["approve"],
        "rejected": decisions["reject"],
        "reject_rate": wilson(decisions["reject"], decisions.total()).model_dump(),
        **_wait_minutes(tasks),
    }


def _stop_events(session: Session, deployment: AgentDeploymentModel, start: datetime) -> list[dict[str, Any]]:
    rows = session.scalars(select(AuditEventModel).where(
        AuditEventModel.object_id == deployment.id, AuditEventModel.action.in_(STOP_ACTIONS),
        AuditEventModel.at >= start).order_by(AuditEventModel.at))
    events = [{"action": r.action, "at": r.at, "reason": (r.detail or {}).get("reason") or (r.detail or {}).get("comment")}
              for r in rows]
    if deployment.status == "killed" and deployment.stopped_at and deployment.stopped_at.replace(tzinfo=None) >= start:
        events.append({"action": "killed", "at": deployment.stopped_at, "reason": deployment.stop_reason})
    return events


def _outcomes(finished: list[AgentExecutionModel]) -> dict[str, Any]:
    """Failures, escalations, time and cost of the cases that ended."""
    latencies = [s for s in (_seconds(e.started_at or e.created_at, e.completed_at) for e in finished) if s is not None]
    costs = [e.total_cost_usd for e in finished if e.total_cost_usd is not None]
    statuses = Counter(e.status for e in finished)
    escalated = statuses["ESCALATED"]
    return {
        "finished": len(finished),
        "error_rate": wilson(statuses["FAILED"] + statuses["TIMED_OUT"], len(finished)).model_dump(),
        "escalation_rate": wilson(escalated, len(finished)).model_dump(),
        "escalations": escalated,
        "latency_seconds": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "cost_usd": round(sum(costs), 6),
        "cost_per_case_usd": round(sum(costs) / len(costs), 6) if costs else None,
    }


def deployment_metrics(session: Session, deployment: AgentDeploymentModel, window: str = "day") -> dict[str, Any]:
    start = since(window)
    executions = executions_in(session, deployment.id, start)
    tools, denied = _tools(session, [e.id for e in executions])
    return {
        "window": window,
        "since": start,
        "executions": len(executions),
        "by_status": dict(Counter(e.status for e in executions)),
        **_outcomes([e for e in executions if e.status in TERMINAL_RUN_STATUSES]),
        "tokens": sum(e.total_tokens or 0 for e in executions),
        "tools": tools,
        "tool_errors": sum(t["errors"] for t in tools.values()),
        "policy_denials": denied,
        "reviews": _reviews(session, deployment.id, start),
        "stops": _stop_events(session, deployment, start),
        "shadow_copies": sum(1 for e in executions if e.shadow_of),
    }


def flat(metrics: dict[str, Any]) -> dict[str, float | None]:
    """The numbers an alert rule can name (US39.14)."""
    return {
        "executions": metrics["executions"],
        "error_rate": metrics["error_rate"]["rate"],
        "escalation_rate": metrics["escalation_rate"]["rate"],
        "latency_p50_seconds": metrics["latency_seconds"]["p50"],
        "latency_p95_seconds": metrics["latency_seconds"]["p95"],
        "cost_usd": metrics["cost_usd"],
        "cost_per_case_usd": metrics["cost_per_case_usd"],
        "tokens": metrics["tokens"],
        "tool_errors": metrics["tool_errors"],
        "policy_denials": metrics["policy_denials"],
        "escalations": metrics["escalations"],
        "reject_rate": metrics["reviews"]["reject_rate"]["rate"],
        "reviews_open": metrics["reviews"]["open"],
        "review_wait_minutes_max": metrics["reviews"]["waiting_now_minutes_max"],
    }


METRIC_NAMES = tuple(flat({
    "executions": 0, "error_rate": {"rate": None}, "escalation_rate": {"rate": None},
    "latency_seconds": {"p50": None, "p95": None}, "cost_usd": 0, "cost_per_case_usd": None, "tokens": 0,
    "tool_errors": 0, "policy_denials": 0, "escalations": 0,
    "reviews": {"reject_rate": {"rate": None}, "open": 0, "waiting_now_minutes_max": 0},
}))


def count_since(session: Session, deployment_id: str, start: datetime) -> int:
    return session.scalar(select(func.count()).select_from(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment_id, AgentExecutionModel.created_at >= start)) or 0
