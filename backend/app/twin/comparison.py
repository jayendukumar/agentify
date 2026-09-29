"""Epic 19, US19.4: a plain-English behavioral diff between two runs --
typically two versions of the same agent (US19.5's `AgentArtifact.version`),
but works for any two runs the caller resolves (`app/db/repository.py`'s
`get_any_run`). Reuses each run's own already-computed `deviations`/
`evaluation` (US19.1) rather than re-deriving anything from the raw steps.
"""

from __future__ import annotations

from app.schemas.evaluation import RunComparison
from app.schemas.run import RunBase


def _tool_sequence(run: RunBase) -> list[str]:
    return [s.target or "" for s in run.steps if s.type in ("TOOL_EXECUTED", "TOOL_FAILED")]


def _human_intervention_count(run: RunBase) -> int:
    return sum(1 for s in run.steps if s.type == "HUMAN_APPROVAL_REQUESTED")


def _duration_seconds(run: RunBase) -> float | None:
    if run.completed_at is None:
        return None
    return (run.completed_at - run.started_at).total_seconds()


def compare_runs(run_a: RunBase, run_b: RunBase) -> RunComparison:
    differences: list[str] = []

    if run_a.status != run_b.status:
        differences.append(f"Outcome differs: run A is '{run_a.status}', run B is '{run_b.status}'.")
    if run_a.graded_passed != run_b.graded_passed:
        differences.append(f"Grading differs: run A graded_passed={run_a.graded_passed}, run B graded_passed={run_b.graded_passed}.")

    tools_a, tools_b = _tool_sequence(run_a), _tool_sequence(run_b)
    if tools_a != tools_b:
        differences.append(f"Different tools/order selected: run A called {tools_a or ['none']}, run B called {tools_b or ['none']}.")
    elif len(tools_a) != len(set(tools_a)):
        differences.append(f"Run A and B both repeated at least one tool call (sequence: {tools_a}).")

    denied_a = sum(1 for s in run_a.steps if s.type == "POLICY_CHECK" and s.denied)
    denied_b = sum(1 for s in run_b.steps if s.type == "POLICY_CHECK" and s.denied)
    if denied_a != denied_b:
        differences.append(f"Policy violations differ: run A had {denied_a}, run B had {denied_b}.")

    human_a, human_b = _human_intervention_count(run_a), _human_intervention_count(run_b)
    if human_a != human_b:
        differences.append(f"Human-intervention needs differ: run A requested {human_a}, run B requested {human_b}.")

    esc_a = sum(1 for s in run_a.steps if s.type == "ESCALATION")
    esc_b = sum(1 for s in run_b.steps if s.type == "ESCALATION")
    if esc_a != esc_b:
        differences.append(f"Escalation decisions differ: run A escalated {esc_a} time(s), run B escalated {esc_b} time(s).")

    duration_a, duration_b = _duration_seconds(run_a), _duration_seconds(run_b)
    # A half-second absolute threshold, not a tight one -- two runs against
    # a fake/instant LLM in tests routinely differ by a few milliseconds of
    # pure scheduling noise, which isn't a "behavioral" difference worth
    # surfacing (found via a real flaky test: two identical happy-path runs
    # occasionally rounded to different tenths of a second).
    if duration_a is not None and duration_b is not None and abs(duration_a - duration_b) > 0.5:
        differences.append(f"Duration differs: run A took {duration_a:.1f}s, run B took {duration_b:.1f}s.")

    cost_a, cost_b = run_a.total_cost_usd, run_b.total_cost_usd
    if cost_a is not None and cost_b is not None and abs(cost_a - cost_b) > 1e-6:
        differences.append(f"Cost differs: run A cost ${cost_a:.4f}, run B cost ${cost_b:.4f}.")

    if run_a.total_tokens != run_b.total_tokens:
        differences.append(f"Tool usage/token volume differs: run A used {run_a.total_tokens} tokens, run B used {run_b.total_tokens}.")

    if run_a.final_output != run_b.final_output:
        differences.append(f"Different final outputs: run A produced {run_a.final_output}, run B produced {run_b.final_output}.")

    if run_a.evaluation is not None and run_b.evaluation is not None:
        by_name_a = {d.name: d.passed for d in run_a.evaluation.dimensions}
        by_name_b = {d.name: d.passed for d in run_b.evaluation.dimensions}
        for name, passed_a in by_name_a.items():
            passed_b = by_name_b.get(name)
            if passed_b is not None and passed_a != passed_b:
                differences.append(f"Evaluation dimension '{name}' differs: run A {'passed' if passed_a else 'failed'}, run B {'passed' if passed_b else 'failed'}.")

    if not differences:
        differences.append("No behavioral differences detected between these two runs.")

    return RunComparison(run_a_id=run_a.id, run_b_id=run_b.id, differences=differences)
