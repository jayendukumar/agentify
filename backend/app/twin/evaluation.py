"""Epic 19, US19.1: computes the twelve named evaluation dimensions for one
completed run, alongside (not replacing) `app/twin/engine.py`'s `grade_run`/
`app/twin/orchestrator.py`'s `grade_orchestration`.

Kept structural per the epic's own Notes: every dimension either reads a
signal that already exists in the run's `RunStep` trace/deviations (the same
principle `grade_run` already follows -- compare the real trace, don't ask
an LLM to re-judge it after the fact) or, where today's scenario schema has
no such signal yet (tool_argument_correctness, grounding), says so plainly
in its `detail` rather than fabricating a check nothing backs -- the same
"flag the gap explicitly" precedent Epic 20 set for its own coverage
(masking, versioned knowledge sources).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.schemas.agents import RuntimeGuardrails
from app.schemas.evaluation import EvaluationDimension, EvaluationResult
from app.schemas.run import RunStatus, RunStep, TwinDeviation

_EXECUTED_TOOL_TYPES = ("TOOL_EXECUTED", "TOOL_FAILED")
_HUMAN_DECISION_TYPES = ("HUMAN_APPROVED", "HUMAN_REJECTED")


def evaluate_run(
    *,
    steps: list[RunStep],
    final_output: dict[str, Any] | None,
    deviations: list[TwinDeviation],
    graded_passed: bool | None,
    status: RunStatus,
    guardrails: RuntimeGuardrails,
    total_cost_usd: float | None,
    total_tokens: int,
    started_at: datetime,
    completed_at: datetime | None,
) -> EvaluationResult:
    reasons = [d.reason for d in deviations]
    dimensions = [
        _task_completion(graded_passed, status),
        _policy_compliance(steps),
        _tool_selection_correctness(reasons),
        _tool_argument_correctness(),
        _output_correctness(reasons),
        _grounding(final_output, status),
        _resilience(steps, status),
        _efficiency(steps, guardrails),
        _human_intervention(steps),
        _latency(started_at, completed_at, guardrails),
        _token_usage(total_tokens, guardrails),
        _cost(total_cost_usd, guardrails),
    ]
    passed_count = sum(1 for d in dimensions if d.passed)
    return EvaluationResult(dimensions=dimensions, passed_count=passed_count, total_count=len(dimensions))


def _task_completion(graded_passed: bool | None, status: RunStatus) -> EvaluationDimension:
    passed = graded_passed if graded_passed is not None else status == "COMPLETED"
    detail = (
        "Trace and final output matched the scenario's expectations."
        if passed
        else "Trace or final output deviated from the scenario's expectations, or the run didn't complete."
    )
    return EvaluationDimension(name="task_completion", passed=passed, detail=detail)


def _policy_compliance(steps: list[RunStep]) -> EvaluationDimension:
    denied = [s for s in steps if s.type == "POLICY_CHECK" and s.denied]
    passed = not denied
    detail = (
        "No tool call was denied by the Tool Gateway."
        if passed
        else f"{len(denied)} tool call(s) were denied by policy: " + "; ".join(s.reason or s.target or "" for s in denied)
    )
    return EvaluationDimension(name="policy_compliance", passed=passed, detail=detail)


def _tool_selection_correctness(reasons: list[str]) -> EvaluationDimension:
    mismatches = [r for r in reasons if r.startswith("Step ") and "tool_call" in r and "expected" in r]
    passed = not mismatches
    detail = "Agent called the expected systems in the expected order." if passed else "; ".join(mismatches)
    return EvaluationDimension(name="tool_selection_correctness", passed=passed, detail=detail)


def _tool_argument_correctness() -> EvaluationDimension:
    return EvaluationDimension(
        name="tool_argument_correctness",
        passed=True,
        detail=(
            "Not independently checked -- today's scenario schema (TwinExpectedStep) records an "
            "expected tool target/kind, not expected call arguments, so there's no structural signal "
            "to grade this dimension against yet."
        ),
    )


def _output_correctness(reasons: list[str]) -> EvaluationDimension:
    mismatches = [r for r in reasons if "output field" in r]
    passed = not mismatches
    detail = "Final output matched every expected field." if passed else "; ".join(mismatches)
    return EvaluationDimension(name="output_correctness", passed=passed, detail=detail)


def _grounding(final_output: dict[str, Any] | None, status: RunStatus) -> EvaluationDimension:
    passed = status != "COMPLETED" or final_output is not None
    detail = (
        "Agent produced a well-formed final answer (structural check only -- content isn't "
        "fact-checked against tool results)."
        if passed
        else "Run completed without producing a parseable final answer."
    )
    return EvaluationDimension(name="grounding", passed=passed, detail=detail)


def _resilience(steps: list[RunStep], status: RunStatus) -> EvaluationDimension:
    failures = [s for s in steps if s.type in ("TOOL_FAILED", "RETRY")]
    if not failures:
        return EvaluationDimension(name="resilience", passed=True, detail="No tool failures or retries occurred.")
    passed = status == "COMPLETED"
    detail = (
        f"Recovered from {len(failures)} tool failure(s)/retry(s) and still completed."
        if passed
        else f"Hit {len(failures)} tool failure(s)/retry(s) and did not complete."
    )
    return EvaluationDimension(name="resilience", passed=passed, detail=detail)


def _efficiency(steps: list[RunStep], guardrails: RuntimeGuardrails) -> EvaluationDimension:
    tool_calls = sum(1 for s in steps if s.type in _EXECUTED_TOOL_TYPES)
    passed = tool_calls <= guardrails.max_tool_calls and len(steps) <= guardrails.max_steps
    detail = f"{tool_calls} tool call(s) / {len(steps)} step(s) (limits: {guardrails.max_tool_calls} / {guardrails.max_steps})."
    return EvaluationDimension(name="efficiency", passed=passed, detail=detail)


def _human_intervention(steps: list[RunStep]) -> EvaluationDimension:
    requested = sum(1 for s in steps if s.type == "HUMAN_APPROVAL_REQUESTED")
    decided = [s for s in steps if s.type in _HUMAN_DECISION_TYPES]
    rejected = sum(1 for s in decided if s.type == "HUMAN_REJECTED")
    detail = (
        "No human checkpoint was hit." if requested == 0 else f"{requested} human checkpoint(s) requested, {rejected} rejected."
    )
    return EvaluationDimension(name="human_intervention", passed=True, detail=detail)


def _latency(started_at: datetime, completed_at: datetime | None, guardrails: RuntimeGuardrails) -> EvaluationDimension:
    if completed_at is None:
        return EvaluationDimension(name="latency", passed=True, detail="Run hasn't completed yet.")
    # started_at as loaded from the DB is naive (TIMESTAMP WITHOUT TIME
    # ZONE); completed_at may still be the tz-aware value this same request
    # just set with app.ids.utcnow() before the round-trip through
    # Postgres strips it -- same mismatch app/db/repository.py's
    # get_twin_summary sidesteps by always reading both sides post-commit.
    # Normalize both to naive UTC here rather than relying on call-site
    # timing.
    if started_at.tzinfo is not None:
        started_at = started_at.replace(tzinfo=None)
    if completed_at.tzinfo is not None:
        completed_at = completed_at.replace(tzinfo=None)
    duration = (completed_at - started_at).total_seconds()
    passed = duration <= guardrails.max_runtime_seconds
    detail = f"{duration:.1f}s (limit: {guardrails.max_runtime_seconds}s)."
    return EvaluationDimension(name="latency", passed=passed, detail=detail)


def _token_usage(total_tokens: int, guardrails: RuntimeGuardrails) -> EvaluationDimension:
    passed = total_tokens <= guardrails.max_tokens
    detail = f"{total_tokens} token(s) (limit: {guardrails.max_tokens})."
    return EvaluationDimension(name="token_usage", passed=passed, detail=detail)


def _cost(total_cost_usd: float | None, guardrails: RuntimeGuardrails) -> EvaluationDimension:
    if total_cost_usd is None:
        return EvaluationDimension(
            name="cost", passed=True, detail="Cost unknown (one or more calls used an unpriced model)."
        )
    passed = total_cost_usd <= guardrails.max_cost_usd
    detail = f"${total_cost_usd:.4f} (limit: ${guardrails.max_cost_usd})."
    return EvaluationDimension(name="cost", passed=passed, detail=detail)
