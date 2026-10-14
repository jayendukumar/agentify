"""Epic 36, US36.6/US36.7: per-case efficiency and the process twin report.

Pure functions over finished orchestration runs and the operating model,
so they're testable without a database or an LLM.

Per case, every task the case passed through is costed twice:

- **As-is:** a person does it -- the wait before it plus their handling
  time, both sampled from the operating model.
- **To-be:** an agent does it in its measured time; when the agent asks for
  review, a reviewer's wait and time are added. A manual step stays manual
  with the same handling time (it's the same work), and the to-be wait
  where one is given.

Both sides of a case use the same random draws (seeded per case), so the
comparison is paired: the difference is the process, not sampling noise.
A case the twin couldn't finish counts as handled fully by hand in the
to-be process -- in production it would fall back to a person -- so a
failing twin never inflates the savings.

Times are working minutes. Cycle time is waits plus handling along the
path. Touch time is people's hands-on time only.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.batches.results import wilson
from app.schemas.process_twin import (
    CaseEfficiency,
    EfficiencyReport,
    Interval,
    NodeEfficiency,
    NodeOperatingModel,
    OperatingModel,
    OutcomeCell,
    SegmentEfficiency,
    Triangular,
)

_BOOTSTRAP_SAMPLES = 1000


@dataclass
class CaseInput:
    """What the report needs from one finished case run."""

    run_id: str
    row_key: str | None
    status: str
    node_runs: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    visited_path: list[str]
    total_cost_usd: float | None
    labels: dict[str, Any] | None
    seed: int
    deviations: list[dict[str, Any]]
    segment: str = "typical"


def sample(dist: Triangular | None, rng: random.Random) -> float:
    if dist is None:
        return 0.0
    if dist.max == dist.min:
        return dist.min
    return rng.triangular(dist.min, dist.max, dist.likely)


def _rate(node: NodeOperatingModel | None, model: OperatingModel) -> float:
    if node is None:
        return 0.0
    if node.hourly_cost is not None:
        return node.hourly_cost
    for role in model.roles:
        if role.role.strip().lower() == node.role.strip().lower():
            return role.hourly_cost
    return 0.0


def _seconds(node_run: dict[str, Any]) -> float:
    start, end = node_run.get("started_at"), node_run.get("completed_at")
    if not start or not end:
        return 0.0
    if isinstance(start, str):
        start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    return max(0.0, (end - start).total_seconds())


def expected_outcome(labels: dict[str, Any] | None, model: OperatingModel) -> str | None:
    if not labels:
        return None
    if model.outcome_label == "decision":
        return labels.get("decision")
    outputs = labels.get("outputs") or {}
    value = outputs.get(model.outcome_label, labels.get(model.outcome_label))
    return None if value is None else str(value)


@dataclass
class _Tally:
    """One case's running totals, both sides."""

    as_is_cycle: float = 0.0
    as_is_touch: float = 0.0
    as_is_cost: float = 0.0
    to_be_cycle: float = 0.0
    to_be_touch: float = 0.0
    to_be_labour: float = 0.0
    agent_seconds: float = 0.0
    touches: int = 0

    def person(self, minutes: float, wait: float, rate: float, times: int = 1) -> None:
        """`times` people-steps of `minutes` each, after `wait` each, on the to-be side."""
        self.to_be_cycle += times * (wait + minutes)
        self.to_be_touch += times * minutes
        self.to_be_labour += times * minutes / 60 * rate
        self.touches += times

    def fall_back_to_manual(self) -> None:
        """A case the twin couldn't finish: in production a person would
        handle it, on top of what the twin already spent."""
        self.to_be_cycle += self.as_is_cycle
        self.to_be_touch += self.as_is_touch
        self.to_be_labour += self.as_is_cost
        self.touches += 1


def _cost_step(tally: _Tally, node_run: dict[str, Any], om: NodeOperatingModel | None, model: OperatingModel,
               reviews: int, rng: random.Random) -> None:
    """Cost one agent or manual step on both sides (draws in a fixed order,
    so both sides of the case share them)."""
    rate = _rate(om, model)
    handling = sample(om.handling_minutes if om else None, rng)
    wait = sample(om.wait_minutes if om else None, rng)
    to_be_wait = sample(om.to_be_wait_minutes, rng) if om and om.to_be_wait_minutes else wait
    review = sample((om.review_minutes if om else None) or model.default_review_minutes, rng)

    tally.as_is_cycle += wait + handling
    tally.as_is_touch += handling
    tally.as_is_cost += handling / 60 * rate
    if node_run.get("kind") == "manual":
        tally.person(handling, to_be_wait, rate)
        return
    seconds = _seconds(node_run)
    tally.agent_seconds += seconds
    tally.to_be_cycle += seconds / 60
    if reviews:
        tally.person(review, to_be_wait, rate, times=reviews)


def case_efficiency(case: CaseInput, model: OperatingModel) -> CaseEfficiency:
    rng = random.Random(case.seed)
    by_node = {n.node_id: n for n in model.nodes}
    reviews = Counter(s.get("node_id") for s in case.steps if s.get("type") == "HUMAN_APPROVAL_REQUESTED")
    completed = case.status == "COMPLETED"

    tally = _Tally()
    end_node: str | None = None
    for node_run in case.node_runs:
        if node_run.get("kind") == "end_event":
            end_node = node_run.get("node_id")
        elif node_run.get("kind") in ("agent", "manual"):
            node_id = node_run.get("node_id")
            _cost_step(tally, node_run, by_node.get(node_id), model, reviews.get(node_id, 0), rng)
    if not completed:
        tally.fall_back_to_manual()

    outcome = model.end_event_outcomes.get(end_node, end_node) if completed and end_node else None
    expected = expected_outcome(case.labels, model)
    failure = None if completed else ("; ".join(d.get("reason", "") for d in case.deviations) or f"run {case.status.lower()}")
    return CaseEfficiency(
        run_id=case.run_id,
        row_key=case.row_key,
        segment=case.segment,
        status=case.status,
        outcome=outcome,
        expected_outcome=expected,
        correct=None if expected is None else (outcome is not None and str(outcome).lower() == str(expected).lower()),
        path=case.visited_path,
        straight_through=completed and tally.touches == 0,
        human_touches=tally.touches,
        as_is_cycle_minutes=round(tally.as_is_cycle, 2),
        to_be_cycle_minutes=round(tally.to_be_cycle, 2),
        as_is_touch_minutes=round(tally.as_is_touch, 2),
        to_be_touch_minutes=round(tally.to_be_touch, 2),
        as_is_cost=round(tally.as_is_cost, 2),
        to_be_labour_cost=round(tally.to_be_labour, 2),
        llm_cost_usd=round(case.total_cost_usd or 0.0, 5),
        agent_seconds=round(tally.agent_seconds, 1),
        failure=failure,
    )


def _mean_interval(values: list[float], rng: random.Random, digits: int = 2) -> Interval:
    """Mean with a 95% bootstrap interval (percentile method)."""
    if not values:
        return Interval(mean=None, low=None, high=None)
    if len(values) == 1:
        v = round(values[0], digits)
        return Interval(mean=v, low=v, high=v)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(_BOOTSTRAP_SAMPLES))
    return Interval(
        mean=round(statistics.fmean(values), digits),
        low=round(means[int(0.025 * _BOOTSTRAP_SAMPLES)], digits),
        high=round(means[int(0.975 * _BOOTSTRAP_SAMPLES) - 1], digits),
    )


def _ratio_interval(before: list[float], after: list[float], rng: random.Random) -> Interval:
    """Fraction saved, (sum before - sum after) / sum before, with a paired
    bootstrap over cases."""
    pairs = list(zip(before, after))
    if not pairs or sum(before) == 0:
        return Interval(mean=None, low=None, high=None)

    def saved(sample_pairs: list[tuple[float, float]]) -> float:
        b = sum(p[0] for p in sample_pairs)
        return (b - sum(p[1] for p in sample_pairs)) / b if b else 0.0

    boots = sorted(saved(rng.choices(pairs, k=len(pairs))) for _ in range(_BOOTSTRAP_SAMPLES))
    return Interval(
        mean=round(saved(pairs), 4),
        low=round(boots[int(0.025 * _BOOTSTRAP_SAMPLES)], 4),
        high=round(boots[int(0.975 * _BOOTSTRAP_SAMPLES) - 1], 4),
    )


def _scale(interval: Interval, factor: float, digits: int = 1) -> Interval:
    return Interval(**{k: None if v is None else round(v * factor, digits) for k, v in interval.model_dump().items()})


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{round(value * 100)}%"


def _mean_or_none(values: list[float], digits: int) -> float | None:
    return round(statistics.fmean(values), digits) if values else None


def _node_breakdown(cases: list[CaseInput], model: OperatingModel, labels_by_node: dict[str, tuple[str, str]]) -> list[NodeEfficiency]:
    """Per step: visits, failures, agent time, review rate, AI cost, and
    people's time today vs with agents (at the most likely values)."""
    runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reviewed: Counter[str] = Counter()
    for case in cases:
        reviewed_nodes = {s.get("node_id") for s in case.steps if s.get("type") == "HUMAN_APPROVAL_REQUESTED"}
        for nr in case.node_runs:
            if nr.get("kind") in ("agent", "manual"):
                runs[nr["node_id"]].append(nr)
                reviewed[nr["node_id"]] += nr["node_id"] in reviewed_nodes and nr["kind"] == "agent"
    by_node = {n.node_id: n for n in model.nodes}
    order = {nid: i for i, nid in enumerate(labels_by_node)}
    nodes = []
    for node_id in sorted(runs, key=lambda nid: order.get(nid, 10**6)):
        node_runs = runs[node_id]
        om = by_node.get(node_id)
        kind = node_runs[0]["kind"]
        agent_runs = [nr for nr in node_runs if nr["kind"] == "agent"]
        likely = om.handling_minutes.likely if om and om.handling_minutes else 0.0
        review = ((om.review_minutes if om else None) or model.default_review_minutes).likely
        review_rate = reviewed[node_id] / len(node_runs)
        nodes.append(NodeEfficiency(
            node_id=node_id,
            label=labels_by_node.get(node_id, (node_id, ""))[0],
            kind=kind,
            visits=len(node_runs),
            failures=sum(nr.get("status") != "COMPLETED" for nr in node_runs),
            mean_agent_seconds=_mean_or_none([_seconds(nr) for nr in agent_runs], 1),
            review_rate=round(review_rate, 3) if kind == "agent" else None,
            mean_llm_cost_usd=_mean_or_none([nr.get("total_cost_usd") or 0.0 for nr in agent_runs], 5),
            as_is_touch_minutes=likely,
            to_be_touch_minutes=likely if kind == "manual" else round(review * review_rate, 2),
        ))
    return nodes


def _segments(rows: list[CaseEfficiency]) -> list[SegmentEfficiency]:
    """Weakest segment first."""
    by_segment: dict[str, list[CaseEfficiency]] = defaultdict(list)
    for r in rows:
        by_segment[r.segment].append(r)
    segments = []
    for name, members in by_segment.items():
        graded = [m for m in members if m.correct is not None]
        segments.append(SegmentEfficiency(
            segment=name,
            cases=len(members),
            accuracy=wilson(sum(bool(m.correct) for m in graded), len(graded)),
            straight_through=round(sum(m.straight_through for m in members) / len(members), 3),
            mean_touch_saved_minutes=round(statistics.fmean(m.as_is_touch_minutes - m.to_be_touch_minutes for m in members), 1),
        ))
    return sorted(segments, key=lambda seg: (seg.accuracy.rate if seg.accuracy.rate is not None else 2, seg.segment))


def build_report(
    cases: list[CaseInput], model: OperatingModel, labels_by_node: dict[str, tuple[str, str]], *, model_version: int = 1
) -> EfficiencyReport:
    """`labels_by_node`: node id -> (label, bpmn type), from the diagram."""
    rng = random.Random(20261013)
    rows = [case_efficiency(c, model) for c in cases]
    completed = [r for r in rows if r.status == "COMPLETED"]
    graded = [r for r in rows if r.correct is not None]
    fx = model.usd_to_currency

    as_is_cycle = [r.as_is_cycle_minutes for r in rows]
    tb_cycle = [r.to_be_cycle_minutes for r in rows]
    as_is_touch = [r.as_is_touch_minutes for r in rows]
    tb_touch = [r.to_be_touch_minutes for r in rows]
    as_is_cost = [r.as_is_cost for r in rows]
    tb_cost = [r.to_be_labour_cost + r.llm_cost_usd * fx for r in rows]
    per_year = model.monthly_volume * 12

    hours = _scale(_mean_interval([(a - b) / 60 for a, b in zip(as_is_touch, tb_touch)], rng, 4), per_year, 0)
    fte = _scale(hours, 1 / model.productive_hours_per_fte, 1)
    annual_cost = _scale(_mean_interval([a - b for a, b in zip(as_is_cost, tb_cost)], rng, 4), per_year, 0)

    matrix = Counter((r.expected_outcome or "unlabelled", r.outcome or f"not finished ({r.status.lower()})") for r in rows)
    variants = Counter(tuple(p for p in r.path if labels_by_node.get(p, ("", ""))[1] != "startEvent") for r in rows)

    stp = wilson(sum(r.straight_through for r in rows), len(rows))
    accuracy = wilson(sum(bool(r.correct) for r in graded), len(graded))
    cycle_cut = _ratio_interval(as_is_cycle, tb_cycle, rng)
    touch_cut = _ratio_interval(as_is_touch, tb_touch, rng)
    headline = (
        f"Over {len(rows)} cases, the to-be process finished {len(completed)} end to end; "
        f"{_pct(stp.rate)} went straight through with no person involved. "
        f"It reached the right outcome in {_pct(accuracy.rate)} of labelled cases "
        f"({_pct(accuracy.low)}-{_pct(accuracy.high)}). People's time per case falls by {_pct(touch_cut.mean)} "
        f"and cycle time by {_pct(cycle_cut.mean)}; at {model.monthly_volume:,} cases a month that frees about "
        f"{fte.mean} FTE ({fte.low}-{fte.high})."
    )
    assumptions = [
        f"Handling and waiting times are sampled from the operating model (v{model_version}), the same draws for both "
        "sides of each case.",
        "Agent time is the measured run time in the twin, including model latency; system calls answer from sample data.",
        f"A case the twin could not finish is counted as handled fully by hand ({len(rows) - len(completed)} cases).",
        f"Annual figures extrapolate the sample mean to {model.monthly_volume:,} cases a month and "
        f"{model.productive_hours_per_fte:,.0f} productive hours per FTE; intervals are 95% bootstrap ranges.",
        f"LLM cost converted at {fx} {model.currency} per USD.",
    ]
    return EfficiencyReport(
        cases=len(rows),
        completed=len(completed),
        failed=len(rows) - len(completed),
        currency=model.currency,
        straight_through=stp,
        accuracy=accuracy,
        as_is_cycle_minutes=_mean_interval(as_is_cycle, rng),
        to_be_cycle_minutes=_mean_interval(tb_cycle, rng),
        cycle_time_reduction=cycle_cut,
        as_is_touch_minutes=_mean_interval(as_is_touch, rng),
        to_be_touch_minutes=_mean_interval(tb_touch, rng),
        touch_time_reduction=touch_cut,
        as_is_cost_per_case=_mean_interval(as_is_cost, rng),
        to_be_cost_per_case=_mean_interval(tb_cost, rng),
        llm_cost_per_case_usd=_mean_interval([r.llm_cost_usd for r in rows], rng, 5),
        annual_hours_released=hours,
        annual_fte_released=fte,
        annual_cost_saved=annual_cost,
        assumptions=assumptions,
        headline=headline,
        nodes=_node_breakdown(cases, model, labels_by_node),
        segments=_segments(rows),
        outcome_matrix=[OutcomeCell(expected=e, actual=a, count=n) for (e, a), n in sorted(matrix.items())],
        path_variants=[{"path": list(p), "cases": n} for p, n in variants.most_common(8)],
        cases_detail=rows,
        usd_to_currency=fx,
    )
