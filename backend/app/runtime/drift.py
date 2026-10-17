"""Epic 39, US39.15: does the agent behave live the way it did in the twin?

**Baseline**, frozen on the deployment when it's created
(`snapshot["baseline"]`): from the latest finished correctness batch of
the approved version (Epic 35) -- pass rate with its Wilson interval,
decision mix, escalation rate, tool-call mix, cost and latency
percentiles, and a profile of the dataset's inputs. A version approved
without batch evidence has no baseline, and the page says so.

**Live check**, over a rolling window (default 7 days, at least 100
finished cases), the same numbers from production:

- decision mix, tool-call mix and each input field: population stability
  index (PSI). Above 0.2 is flagged -- the usual reading is "under 0.1
  stable, 0.1-0.2 moving, over 0.2 shifted";
- escalation rate and accuracy: flagged when the live 95% interval and
  the twin's don't overlap;
- cost and latency: flagged when the live median falls outside the twin's
  5th-95th percentile.

Accuracy uses the cases whose real outcome is known (a reviewer's answer,
the caller's reported outcome; app/runtime/rollout.py `known_outcome`).

Input drift is reported apart from behaviour drift, so "the cases
changed" can be told from "the agent changed". Each check's rule says
whether a flag only notifies or also pauses (US39.12).
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.batches.results import _duration, _graded, _passed, _percentile, wilson
from app.datasets import service as datasets
from app.db.models import (
    AgentDeploymentModel,
    AgentExecutionModel,
    BatchRunModel,
    ExecutionStepModel,
    TwinScenarioModel,
)
from app.ids import utcnow
from app.schemas.run import TERMINAL_RUN_STATUSES

from .rollout import decision_of, known_outcome

PSI_FLAG = 0.2
DEFAULT_RULE = {"enabled": True, "action": "notify", "window_days": 7, "min_executions": 100}
_EPSILON = 1e-4
_TOP_VALUES = 10
_MAX_SAMPLE = 2000


# ---- distributions ------------------------------------------------------------------


def psi(expected: dict[str, float], actual: dict[str, float]) -> float:
    """Population stability index between two sets of shares."""
    total = 0.0
    for key in set(expected) | set(actual):
        e, a = max(expected.get(key, 0.0), _EPSILON), max(actual.get(key, 0.0), _EPSILON)
        total += (a - e) * math.log(a / e)
    return round(total, 4)


def shares(counts: dict[str, int] | Counter) -> dict[str, float]:
    n = sum(counts.values())
    return {k: round(v / n, 4) for k, v in counts.items()} if n else {}


def _numeric(values: list[Any]) -> bool:
    present = [v for v in values if v is not None]
    return bool(present) and all(_number(v) for v in present)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _label(value: Any) -> str:
    return "(missing)" if value is None else str(value)[:100]


def profile_field(values: list[Any]) -> dict[str, Any]:
    """Numbers: decile edges and the share in each bin. Anything else: the
    most common values, with the rest as "(other)"."""
    if _numeric(values):
        numbers = sorted(float(v) for v in values if v is not None)
        edges = sorted({_percentile(numbers, q / 10) for q in range(1, 10)})
        return {"kind": "number", "edges": edges, "shares": shares(Counter(_bin(v, edges) for v in values))}
    counts = Counter(_label(v) for v in values)
    top = dict(counts.most_common(_TOP_VALUES))
    rest = sum(counts.values()) - sum(top.values())
    if rest:
        top["(other)"] = rest
    return {"kind": "category", "values": sorted(k for k in top if k != "(other)"), "shares": shares(top)}


def _bin(value: Any, edges: list[float]) -> str:
    if not _number(value):
        return "(missing)"
    return f"bin{sum(1 for e in edges if value > e)}"


def live_shares(profile: dict[str, Any], values: list[Any]) -> dict[str, float]:
    if profile["kind"] == "number":
        return shares(Counter(_bin(v, profile["edges"]) for v in values))
    known = set(profile["values"]) | {"(missing)"}
    return shares(Counter(_label(v) if _label(v) in known else "(other)" for v in values))


def _present(values: Any) -> list[Any]:
    return [v for v in values if v is not None]


def _spread(values: list[float]) -> dict[str, float | None]:
    return {"p05": _percentile(values, 0.05), "p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95)}


# ---- the baseline, at deployment ----------------------------------------------------


def _tool_calls(steps: list[dict[str, Any]]) -> Counter:
    return Counter(s.get("target") or "?" for s in steps or [] if s.get("type") in ("TOOL_EXECUTED", "TOOL_FAILED"))


def _latest_batch(session: Session, artifact_id: str, version: int) -> BatchRunModel | None:
    return session.scalar(select(BatchRunModel).where(
        BatchRunModel.agent_artifact_id == artifact_id, BatchRunModel.agent_version == version,
        BatchRunModel.mode == "correctness", BatchRunModel.status == "completed")
        .order_by(BatchRunModel.finished_at.desc().nullslast(), BatchRunModel.created_at.desc()).limit(1))


def _runs_profile(runs: list[Any], field: str) -> dict[str, Any]:
    """What the twin's runs did: the same numbers the live check computes."""
    finished = [r for r in runs if r.status in TERMINAL_RUN_STATUSES]
    tools: Counter = Counter()
    for run in finished:
        tools.update(_tool_calls(run.steps))
    durations = _present(_duration(r) for r in finished)
    return {
        "runs": len(finished),
        "pass_rate": wilson(sum(_passed(r) for r in runs), len(runs)).model_dump(),
        "decision_mix": shares(Counter(decision_of(r, field) for r in finished)),
        "escalation_rate": wilson(sum(r.status == "ESCALATED" for r in finished), len(finished)).model_dump(),
        "tool_mix": shares(tools),
        "cost_usd": _spread(_present(r.total_cost_usd for r in finished)),
        "latency_seconds": _spread(durations),
    }


def baseline(session: Session, artifact_id: str, version: int, input_fields: list[str]) -> dict[str, Any]:
    from app.db.models import SimulationRunModel

    batch = _latest_batch(session, artifact_id, version)
    if batch is None:
        return {"available": False,
                "reason": f"v{version} was approved without a finished correctness batch, so there's nothing from "
                          "the twin to compare live behaviour with"}
    scenario = session.get(TwinScenarioModel, batch.scenario_id)
    field = scenario.decision_field if scenario is not None else "decision"
    runs = _graded(list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.batch_id == batch.id))))
    rows = datasets.rows_at(session, batch.dataset_id, batch.dataset_version)
    return {
        "available": True, "batch_id": batch.id, "dataset_id": batch.dataset_id,
        "dataset_version": batch.dataset_version, "frozen_at": utcnow().isoformat(), "decision_field": field,
        **_runs_profile(runs, field),
        "inputs": {name: profile_field([(row.inputs or {}).get(name) for row in rows]) for name in input_fields},
    }


# ---- the live check -----------------------------------------------------------------


def rule(deployment: AgentDeploymentModel) -> dict[str, Any]:
    return {**DEFAULT_RULE, **((deployment.pause_rules or {}).get("drift") or {})}


def window_cases(session: Session, deployment: AgentDeploymentModel, days: int) -> list[AgentExecutionModel]:
    start = utcnow().replace(tzinfo=None) - timedelta(days=days)
    return list(session.scalars(select(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment.id, AgentExecutionModel.created_at >= start,
        AgentExecutionModel.status.in_(TERMINAL_RUN_STATUSES - {"KILLED"}))
        .order_by(AgentExecutionModel.created_at.desc()).limit(_MAX_SAMPLE)))


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a["low"] is None or b["low"] is None or not (b["low"] > a["high"] or b["high"] < a["low"])


def _finding(metric: str, base: Any, live: Any, statistic: str, flagged: bool, reading: str) -> dict[str, Any]:
    return {"metric": metric, "baseline": base, "live": live, "statistic": statistic, "flagged": flagged,
            "reading": reading}


def _mix_finding(metric: str, label: str, base: dict[str, float], live: dict[str, float]) -> dict[str, Any] | None:
    if not base and not live:
        return None
    value = psi(base, live)
    return _finding(metric, base, live, f"PSI {value}", value > PSI_FLAG,
                    f"The {label} has shifted (PSI {value})." if value > PSI_FLAG else f"The {label} is as in the twin.")


def _rate_finding(metric: str, label: str, base: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    flagged = not _overlap(base, live)
    pct = lambda r: "n/a" if r["rate"] is None else f"{round(r['rate'] * 100)}%"  # noqa: E731
    reading = (f"The {label} is {pct(live)} live against {pct(base)} in the twin, outside its range."
               if flagged else f"The {label} ({pct(live)}) is within the twin's range ({pct(base)}).")
    return _finding(metric, base, live, "95% intervals" + (" don't overlap" if flagged else " overlap"), flagged, reading)


def _spread_finding(metric: str, label: str, base: dict[str, Any], live: list[float]) -> dict[str, Any] | None:
    median = _percentile(live, 0.5)
    if median is None or base.get("p05") is None or base.get("p95") is None:
        return None
    flagged = not base["p05"] <= median <= base["p95"]
    return _finding(metric, base, {"p50": median, "p95": _percentile(live, 0.95)}, "live median vs twin p05-p95",
                    flagged, f"The median {label} ({median:g}) is " + ("outside" if flagged else "within")
                    + f" the twin's {base['p05']:g}-{base['p95']:g}.")


def _live_tools(session: Session, ids: list[str]) -> Counter:
    if not ids:
        return Counter()
    return Counter(t or "?" for t in session.scalars(select(ExecutionStepModel.target).where(
        ExecutionStepModel.execution_id.in_(ids), ExecutionStepModel.type.in_(("TOOL_EXECUTED", "TOOL_FAILED")))))


def _accuracy(session: Session, cases: list[AgentExecutionModel], field: str, base: dict[str, Any]) -> dict[str, Any]:
    judged = right = 0
    for case in cases:
        truth = known_outcome(session, case, field)
        if truth is not None:
            judged += 1
            right += decision_of(case, field) == truth
    live = wilson(right, judged).model_dump()
    if not judged:
        return _finding("accuracy", base, live, "no known outcomes", False,
                        "No live outcome is known yet (reviews, reported outcomes), so accuracy can't be compared.")
    flagged = live["high"] < base["low"] if base.get("low") is not None else False
    return _finding("accuracy", base, live, "95% intervals", flagged,
                    f"Live accuracy is {round(live['rate'] * 100)}% on {judged} case(s) with a known outcome, "
                    f"against {round((base['rate'] or 0) * 100)}% in the twin"
                    + (" -- clearly lower." if flagged else "."))


def _case_seconds(case: AgentExecutionModel) -> float | None:
    if case.completed_at is None:
        return None
    return (case.completed_at - (case.started_at or case.created_at)).total_seconds()


def _behaviour(session: Session, cases: list[AgentExecutionModel], base: dict[str, Any]) -> list[dict[str, Any]]:
    field = base.get("decision_field") or "decision"
    costs = _present(c.total_cost_usd for c in cases)
    latencies = _present(_case_seconds(c) for c in cases)
    findings = (
        _mix_finding("decision_mix", "decision mix", base["decision_mix"],
                     shares(Counter(decision_of(c, field) for c in cases))),
        _rate_finding("escalation_rate", "escalation rate", base["escalation_rate"],
                      wilson(sum(c.status == "ESCALATED" for c in cases), len(cases)).model_dump()),
        _mix_finding("tool_mix", "mix of tool calls", base["tool_mix"],
                     shares(_live_tools(session, [c.id for c in cases]))),
        _spread_finding("cost_usd", "cost per case", base["cost_usd"], costs),
        _spread_finding("latency_seconds", "time per case (s)", base["latency_seconds"], latencies),
        _accuracy(session, cases, field, base["pass_rate"]),
    )
    return [f for f in findings if f is not None]


def _inputs(cases: list[AgentExecutionModel], base: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, profile in (base.get("inputs") or {}).items():
        live = live_shares(profile, [(c.input or {}).get(name) for c in cases])
        value = psi(profile["shares"], live)
        shifted = value > PSI_FLAG
        out.append(_finding(f"input:{name}", profile["shares"], live, f"PSI {value}", shifted,
                            f"'{name}' looks {'different from' if shifted else 'like'} the twin's dataset (PSI {value})."))
    return out


def _check_reading(flagged: list[dict[str, Any]], shifted: list[dict[str, Any]]) -> str:
    if flagged:
        return ("The agent behaves differently live: " + " ".join(f["reading"] for f in flagged)
                + (" The cases changed too, which may explain it." if shifted else ""))
    if shifted:
        changed = ", ".join(f["metric"].removeprefix("input:") for f in shifted)
        return f"The cases changed ({changed}), but the agent still behaves as in the twin."
    return "Live behaviour matches the twin."


def baseline_of(deployment: AgentDeploymentModel) -> dict[str, Any]:
    return (deployment.snapshot or {}).get("baseline") or {"available": False,
                                                           "reason": "This deployment predates baselines"}


def check(session: Session, deployment: AgentDeploymentModel) -> dict[str, Any]:
    """The live numbers against the frozen baseline, and what's flagged."""
    base = baseline_of(deployment)
    settings = rule(deployment)
    out: dict[str, Any] = {"checked_at": utcnow().isoformat(), "window_days": settings["window_days"],
                           "min_executions": settings["min_executions"], "action": settings["action"],
                           "baseline": base}
    if not base.get("available"):
        return {**out, "status": "no_baseline", "reading": base.get("reason")}
    cases = window_cases(session, deployment, settings["window_days"])
    out["cases"] = len(cases)
    if len(cases) < settings["min_executions"]:
        return {**out, "status": "not_enough",
                "reading": f"{len(cases)} finished case(s) in the last {settings['window_days']} days; drift is "
                           f"checked from {settings['min_executions']}."}
    behaviour, inputs = _behaviour(session, cases, base), _inputs(cases, base)
    flagged = [f for f in behaviour if f["flagged"]]
    shifted = [f for f in inputs if f["flagged"]]
    status = "drift" if flagged else ("input_drift" if shifted else "ok")
    return {**out, "status": status, "behaviour": behaviour, "inputs": inputs,
            "reading": _check_reading(flagged, shifted), "flagged": [f["metric"] for f in flagged + shifted]}


def to_dataset_rows(session: Session, deployment: AgentDeploymentModel, limit: int = 50) -> list[dict[str, Any]]:
    """Epic 32: the window's cases as dataset rows, labelled where the real
    outcome is known, so the twin learns from what production saw."""
    field = ((deployment.snapshot or {}).get("baseline") or {}).get("decision_field") or "decision"
    rows = []
    for case in window_cases(session, deployment, rule(deployment)["window_days"])[:limit]:
        truth = known_outcome(session, case, field)
        labels: dict[str, Any] = {}
        if truth in ("approve", "reject", "escalate"):
            labels["decision"] = truth
        rows.append({"inputs": case.input or {}, "labels": labels, "segment": "production",
                     "provenance": "expert" if labels else "historical",
                     "tags": ["from-production", "drift-window", case.id] + ([] if labels else ["unlabelled"]),
                     "row_key": f"prod-{case.id}"})
    return rows
