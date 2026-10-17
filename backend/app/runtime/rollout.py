"""Epic 39, US39.13: is the new version ready to take more cases?

`comparison` sets a shadow or canary deployment against the version it
would replace, over the cases since the new one started:

- **Each version on its own cases:** decision mix, escalation rate, error
  rate (Wilson 95%), cost per case.
- **Paired, where the same case ran on both** (a shadow copy and the live
  case it copies): how often they reached the same decision, and -- where
  what really happened is known (the caller's reported outcome, or a
  reviewer's answer on the live case) -- which version got it right, with
  Epic 35's McNemar exact test on the cases one fixed and the other broke.

A canary's cases don't overlap the stable version's (each case goes to
one of them), so a canary is compared on rates only, and the reading
says so.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.batches.results import mcnemar_p, wilson
from app.datasets.runtime import decision_from_output
from app.db.models import AgentDeploymentModel, AgentExecutionModel, ReviewTaskModel
from app.schemas.run import TERMINAL_RUN_STATUSES


def decision_field(deployment: AgentDeploymentModel) -> str:
    return ((deployment.snapshot or {}).get("baseline") or {}).get("decision_field") or "decision"


def decision_of(execution: AgentExecutionModel, field: str) -> str:
    if execution.status == "ESCALATED":
        return "escalate"
    if execution.status in ("FAILED", "TIMED_OUT", "KILLED"):
        return "error"
    return decision_from_output(execution.final_output, field) or "none"


def _stated(outcome: dict[str, Any] | None, field: str) -> str | None:
    """The decision an outcome states, under "decision" or the agent's field."""
    outcome = outcome or {}
    key = "decision" if outcome.get("decision") else field
    if not outcome.get(key):
        return None
    return decision_from_output(outcome, key) or str(outcome[key])


def known_outcome(session: Session, execution: AgentExecutionModel, field: str) -> str | None:
    """What really happened to the case, when someone said: the caller's
    reported outcome, a person's answer on its escalation, or a reviewer
    approving what it proposed (a rejection says it was wrong, not what
    was right)."""
    reported = _stated(execution.reported_outcome, field)
    if reported is not None:
        return reported
    task = session.scalar(select(ReviewTaskModel).where(
        ReviewTaskModel.execution_id == execution.id, ReviewTaskModel.status.in_(("decided", "resolved")))
        .order_by(ReviewTaskModel.created_at.desc()).limit(1))
    if task is None:
        return None
    resolved = _stated(task.outcome, field)
    if resolved is not None:
        return resolved
    return decision_of(execution, field) if task.decision == "approve" else None


def _version_numbers(executions: list[AgentExecutionModel], field: str) -> dict[str, Any]:
    finished = [e for e in executions if e.status in TERMINAL_RUN_STATUSES]
    costs = [e.total_cost_usd for e in finished if e.total_cost_usd is not None]
    return {
        "cases": len(finished),
        "decision_mix": dict(Counter(decision_of(e, field) for e in finished)),
        "escalation_rate": wilson(sum(e.status == "ESCALATED" for e in finished), len(finished)).model_dump(),
        "error_rate": wilson(sum(e.status in ("FAILED", "TIMED_OUT") for e in finished), len(finished)).model_dump(),
        "cost_per_case_usd": round(sum(costs) / len(costs), 6) if costs else None,
    }


def _known_accuracy(session: Session, cases: list[AgentExecutionModel], field: str) -> dict[str, Any]:
    """How often the version got it right where the real outcome is known
    (for a shadow copy, the outcome of the live case it copies)."""
    judged = right = 0
    for case in cases:
        source = session.get(AgentExecutionModel, case.shadow_of) if case.shadow_of else case
        truth = known_outcome(session, source, field) if source is not None else None
        if truth is not None:
            judged += 1
            right += decision_of(case, field) == truth
    return wilson(right, judged).model_dump()


def _finished_since(session: Session, deployment_id: str, start, *, shadow: bool | None) -> list[AgentExecutionModel]:
    query = select(AgentExecutionModel).where(AgentExecutionModel.deployment_id == deployment_id,
                                              AgentExecutionModel.status.in_(TERMINAL_RUN_STATUSES))
    if start is not None:
        query = query.where(AgentExecutionModel.created_at >= start)
    if shadow is True:
        query = query.where(AgentExecutionModel.shadow_of.is_not(None))
    elif shadow is False:
        query = query.where(AgentExecutionModel.shadow_of.is_(None))
    return list(session.scalars(query))


def _judge(session: Session, pairs: list[tuple[AgentExecutionModel, AgentExecutionModel]], field: str) -> dict[str, Any]:
    """Where the real outcome is known: which version got each case right."""
    fixed, regressed, both_right, both_wrong = [], [], 0, 0
    for old, new in pairs:
        truth = known_outcome(session, old, field)
        if truth is None:
            continue
        old_right, new_right = decision_of(old, field) == truth, decision_of(new, field) == truth
        if new_right != old_right:
            (fixed if new_right else regressed).append(old.idempotency_key)
        elif old_right:
            both_right += 1
        else:
            both_wrong += 1
    judged = len(fixed) + len(regressed) + both_right + both_wrong
    return {
        "with_known_outcome": judged,
        "fixed": fixed[:50],
        "regressed": regressed[:50],
        "accuracy_old": wilson(both_right + len(regressed), judged).model_dump(),
        "accuracy_new": wilson(both_right + len(fixed), judged).model_dump(),
        "p_value": mcnemar_p(len(fixed), len(regressed)),
    }


def _paired(session: Session, new_cases: list[AgentExecutionModel], field: str) -> dict[str, Any]:
    live = {e.id: e for e in session.scalars(select(AgentExecutionModel).where(
        AgentExecutionModel.id.in_([c.shadow_of for c in new_cases if c.shadow_of]),
        AgentExecutionModel.status.in_(TERMINAL_RUN_STATUSES)))}
    pairs = [(live[c.shadow_of], c) for c in new_cases if c.shadow_of in live]
    agree = sum(decision_of(a, field) == decision_of(b, field) for a, b in pairs)
    return {"pairs": len(pairs), "agreement": wilson(agree, len(pairs)).model_dump(), **_judge(session, pairs, field)}


def _first_version_reading(new: AgentDeploymentModel, numbers: dict[str, Any]) -> str:
    cases, known = numbers["new"]["cases"], numbers["new"]["accuracy"]
    reading = (f"No earlier version runs, so there is nothing to compare with: v{new.artifact_version} has "
               f"handled {cases} case(s) in shadow.")
    if known["total"]:
        return reading + (f" Where the people's outcome was reported ({known['total']} case(s)), it reached the "
                          f"same decision {round(known['rate'] * 100)}% of the time.")
    if cases:
        return reading + " Report what the people decided on these cases to see how often it agreed."
    return reading


def _paired_reading(new: AgentDeploymentModel, old: AgentDeploymentModel, paired: dict[str, Any]) -> str:
    reading = (f"On {paired['pairs']} case(s) both versions saw, they reached the same decision "
               f"{round((paired['agreement']['rate'] or 0) * 100)}% of the time.")
    if not paired["with_known_outcome"]:
        return reading
    changed = len(paired["fixed"]) + len(paired["regressed"])
    if not changed:
        verdict = "."
    elif paired["p_value"] < 0.05:
        verdict = f": a real difference (p = {paired['p_value']})."
    else:
        verdict = f": not enough to call it a real difference yet (p = {paired['p_value']})."
    return (f"{reading} Where the real outcome is known ({paired['with_known_outcome']} case(s)), "
            f"v{new.artifact_version} got {len(paired['fixed'])} right that v{old.artifact_version} got wrong and "
            f"{len(paired['regressed'])} the other way{verdict}")


def _rate_changes(numbers: dict[str, Any]) -> list[str]:
    out = []
    for label, key in (("escalation", "escalation_rate"), ("error", "error_rate")):
        a, b = numbers["old"][key], numbers["new"][key]
        if a["rate"] is None or b["rate"] is None or not (b["low"] > a["high"] or b["high"] < a["low"]):
            continue
        direction = "higher" if b["rate"] > a["rate"] else "lower"
        out.append(f"The {label} rate is clearly {direction} ({round(b['rate'] * 100)}% against "
                   f"{round(a['rate'] * 100)}%).")
    return out


def _reading(new: AgentDeploymentModel, old: AgentDeploymentModel | None, numbers: dict[str, Any],
             paired: dict[str, Any] | None) -> str:
    if old is None:
        return _first_version_reading(new, numbers)
    if paired is not None and paired["pairs"]:
        first = _paired_reading(new, old, paired)
    elif new.status != "shadow":
        first = "A canary's cases don't overlap the stable version's, so the versions are compared on rates only."
    else:
        first = "No case has finished on both versions yet."
    return " ".join([first, *_rate_changes(numbers)])


def _previous(session: Session, deployment: AgentDeploymentModel) -> AgentDeploymentModel | None:
    if not deployment.previous_deployment_id:
        return None
    return session.get(AgentDeploymentModel, deployment.previous_deployment_id)


def _stage(deployment: AgentDeploymentModel) -> str:
    if deployment.status == "shadow":
        return "shadow"
    return f"canary {deployment.traffic_share}%" if deployment.traffic_share else deployment.status


def comparison(session: Session, deployment: AgentDeploymentModel) -> dict[str, Any]:
    field = decision_field(deployment)
    old = _previous(session, deployment)
    start = deployment.shadow_started_at or deployment.activated_at
    new_cases = _finished_since(session, deployment.id, start, shadow=None)
    old_cases = _finished_since(session, old.id, start, shadow=False) if old is not None else []
    numbers = {"new": _version_numbers(new_cases, field), "old": _version_numbers(old_cases, field) if old else None}
    numbers["new"]["accuracy"] = _known_accuracy(session, new_cases, field)
    paired = _paired(session, [c for c in new_cases if c.shadow_of], field) if old is not None else None
    return {
        "deployment_id": deployment.id,
        "version": deployment.artifact_version,
        "against": ({"deployment_id": old.id, "version": old.artifact_version, "status": old.status}
                    if old is not None else None),
        "stage": _stage(deployment),
        "decision_field": field,
        "numbers": numbers,
        "paired": paired,
        "reading": _reading(deployment, old, numbers, paired),
        "method": "Wilson 95% intervals for rates; McNemar's exact test on cases one version fixed and the other "
                  "broke, where both saw the case and the real outcome is known.",
    }
