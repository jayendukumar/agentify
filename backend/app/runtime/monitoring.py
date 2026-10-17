"""Epic 39, US39.14/US39.15: what runs after every case ends -- alert rules,
the drift check, and the trace export.

**Alert rules** (per deployment, `alert_rules`): a metric from
app/runtime/metrics.py over the last hour, day or week, compared with a
threshold, e.g. `{"metric": "error_rate", "window": "hour", "op": ">",
"threshold": 0.1, "min_executions": 20, "webhook": "https://..."}`.
When one is broken it fires at most once an hour: the deployment's owners
get an in-app notification, a webhook (if set) gets a POST naming the
deployment and linking to it, and a rule marked `"action": "pause"` also
pauses the deployment (US39.12). Rules are checked at most every 30
seconds per deployment, so a burst of cases doesn't recompute a week of
numbers each time.

**Drift** (app/runtime/drift.py) is checked at most once an hour. A new
flag notifies the owners; with the drift rule's `"action": "pause"`, a
change in the agent's behaviour (not only in its inputs) also pauses it.
"""

from __future__ import annotations

import operator
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AgentDeploymentModel, AgentExecutionModel
from app.ids import new_id, utcnow
from app.jobs import queue
from app.jobs.worker import handler

from . import drift, metrics, reviews, telemetry
from .adapters import UnsafeProductionEndpoint, check_production_url

ALERT_JOB_KIND = "runtime_alert"
ALERT_EVERY = timedelta(hours=1)
ALERTS_CHECKED_EVERY = timedelta(seconds=30)
DRIFT_CHECKED_EVERY = timedelta(hours=1)
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


class AlertRuleError(ValueError):
    pass


def _rule_problem(rule: dict[str, Any]) -> str | None:
    checks = (
        (rule.get("metric") in metrics.METRIC_NAMES, f"metric must be one of {', '.join(metrics.METRIC_NAMES)}"),
        (rule.get("window", "hour") in metrics.WINDOWS, "window must be hour, day or week"),
        (rule.get("op", ">") in OPS, f"op must be one of {', '.join(OPS)}"),
        (isinstance(rule.get("threshold"), (int, float)), "threshold must be a number"),
        (rule.get("action", "notify") in ("notify", "pause"), "action must be notify or pause"),
        (not rule.get("email"), "email delivery isn't available yet; use a webhook (owners are always notified "
                                "in the app)"),
    )
    problem = next((why for ok, why in checks if not ok), None)
    if problem is None and rule.get("webhook"):
        try:
            check_production_url(rule["webhook"])
        except UnsafeProductionEndpoint as exc:
            problem = f"webhook {exc}"
    return problem


def check_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validated rules, each with an id."""
    out = []
    for i, rule in enumerate(rules):
        problem = _rule_problem(rule)
        if problem:
            raise AlertRuleError(f"rule {i + 1}: {problem}")
        out.append({"id": rule.get("id") or new_id("alert"), "metric": rule["metric"],
                    "window": rule.get("window", "hour"), "op": rule.get("op", ">"),
                    "threshold": float(rule["threshold"]), "min_executions": int(rule.get("min_executions", 0)),
                    "webhook": rule.get("webhook") or None, "action": rule.get("action", "notify"),
                    "name": rule.get("name") or None})
    return out


class SettingsError(ValueError):
    pass


def apply_settings(deployment: AgentDeploymentModel, *, alert_rules: list[dict[str, Any]] | None,
                   telemetry_settings: dict[str, Any] | None, drift_rule: dict[str, Any] | None) -> None:
    """An admin's alert rules, trace endpoint and drift rule; None leaves
    one as it is. Raises SettingsError with the reason."""
    try:
        if alert_rules is not None:
            deployment.alert_rules = check_rules(alert_rules)
        if telemetry_settings is not None:
            telemetry.check_settings(telemetry_settings)
            deployment.telemetry = telemetry.kept(telemetry_settings)
    except (AlertRuleError, telemetry.TelemetryError) as exc:
        raise SettingsError(str(exc)) from exc
    if drift_rule is not None:
        _apply_drift_rule(deployment, drift_rule)


def _apply_drift_rule(deployment: AgentDeploymentModel, drift_rule: dict[str, Any]) -> None:
    if drift_rule.get("action", "notify") not in ("notify", "pause"):
        raise SettingsError("drift action must be notify or pause")
    allowed = {k: v for k, v in drift_rule.items() if k in drift.DEFAULT_RULE}
    deployment.pause_rules = {**(deployment.pause_rules or {}), "drift": {**drift.rule(deployment), **allowed}}


def _now() -> datetime:
    return utcnow().replace(tzinfo=None)


def _due(state: dict[str, Any], key: str, every: timedelta) -> bool:
    at = state.get(key)
    return at is None or _now() - datetime.fromisoformat(at) >= every


def link(deployment: AgentDeploymentModel) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}/runtime?deployment={deployment.id}"


def _label(deployment: AgentDeploymentModel) -> str:
    return f"{deployment.snapshot['definition']['name']} v{deployment.artifact_version}"


def _broken(rule: dict[str, Any], numbers: dict[str, Any]) -> float | None:
    """The metric's value when the rule is broken, else None."""
    value = metrics.flat(numbers).get(rule["metric"])
    if value is None or numbers["executions"] < rule.get("min_executions", 0):
        return None
    return value if OPS[rule["op"]](value, rule["threshold"]) else None


def _fire(session: Session, deployment: AgentDeploymentModel, rule: dict[str, Any], value: float) -> dict[str, Any]:
    alert = {"rule": rule, "value": value, "deployment_id": deployment.id, "deployment": _label(deployment),
             "process_id": deployment.process_id, "link": link(deployment), "at": _now().isoformat(),
             "message": f"{_label(deployment)}: {rule.get('name') or rule['metric']} is {value:g} over the last "
                        f"{rule.get('window', 'hour')} ({rule['op']} {rule['threshold']:g})"}
    reviews.notify(session, reviews.owners(deployment), kind="alert", message=alert["message"],
                   process_id=deployment.process_id, deployment_id=deployment.id)
    if rule.get("webhook"):
        queue.enqueue(session, ALERT_JOB_KIND, {"url": rule["webhook"], "alert": alert}, max_attempts=5,
                      dedupe_key=f"runtime-alert:{deployment.id}:{rule['id']}:{alert['at']}")
    if rule.get("action") == "pause" and deployment.status == "active":
        from .deployments import pause

        pause(session, deployment, reason=f"Paused automatically by an alert: {alert['message']}", actor="auto")
    return alert


def _alerts_due(deployment: AgentDeploymentModel, state: dict[str, Any], force: bool) -> bool:
    return bool(deployment.alert_rules) and (force or _due(state, "alerts_checked_at", ALERTS_CHECKED_EVERY))


def evaluate_alerts(session: Session, deployment: AgentDeploymentModel, *, force: bool = False) -> list[dict[str, Any]]:
    """Fires the rules this deployment now breaks; returns what fired."""
    state = dict(deployment.monitor_state or {})
    if not _alerts_due(deployment, state, force):
        return []
    state["alerts_checked_at"] = _now().isoformat()
    fired_at = dict(state.get("alerts_fired") or {})
    numbers: dict[str, dict[str, Any]] = {}
    fired = []
    for rule in deployment.alert_rules:
        window = rule.get("window", "hour")
        if window not in numbers:
            numbers[window] = metrics.deployment_metrics(session, deployment, window)
        value = _broken(rule, numbers[window])
        if value is None or not _due(fired_at, rule["id"], ALERT_EVERY):
            continue
        fired_at[rule["id"]] = _now().isoformat()
        fired.append(_fire(session, deployment, rule, value))
    state["alerts_fired"] = fired_at
    state["last_alerts"] = (fired + list(state.get("last_alerts") or []))[:20]
    deployment.monitor_state = state
    session.flush()
    return fired


def _act_on_drift(session: Session, deployment: AgentDeploymentModel, result: dict[str, Any], action: str) -> None:
    reviews.notify(session, reviews.owners(deployment), kind="drift",
                   message=f"{_label(deployment)} drifted from the twin: {result['reading']}",
                   process_id=deployment.process_id, deployment_id=deployment.id)
    if action == "pause" and result["status"] == "drift" and deployment.status == "active":
        from .deployments import pause

        pause(session, deployment, reason=f"Paused automatically: drift from the twin -- {result['reading']}",
              actor="auto")


def check_drift(session: Session, deployment: AgentDeploymentModel, *, force: bool = False) -> dict[str, Any] | None:
    state = dict(deployment.monitor_state or {})
    settings = drift.rule(deployment)
    if not settings.get("enabled") or not (force or _due(state, "drift_checked_at", DRIFT_CHECKED_EVERY)):
        return None
    result = drift.check(session, deployment)
    previous = set((state.get("drift") or {}).get("flagged") or [])
    state["drift_checked_at"] = _now().isoformat()
    state["drift"] = {"status": result["status"], "flagged": result.get("flagged", []), "reading": result.get("reading"),
                      "checked_at": result["checked_at"], "cases": result.get("cases")}
    deployment.monitor_state = state
    if set(result.get("flagged", [])) - previous:
        _act_on_drift(session, deployment, result, settings.get("action"))
    session.flush()
    return result


def after_case(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel) -> None:
    telemetry.queue_export(session, deployment, execution)
    evaluate_alerts(session, deployment)
    check_drift(session, deployment)


@handler(ALERT_JOB_KIND)
async def runtime_alert(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    del session
    check_production_url(payload["url"])
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        response = await client.post(payload["url"], json={"type": "axyntro.runtime.alert", **payload["alert"]})
    if response.status_code >= 300:
        raise RuntimeError(f"The alert webhook answered {response.status_code}")
    return {"status_code": response.status_code}
