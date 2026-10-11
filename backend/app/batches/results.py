"""Epic 35: results at volume and the statistical release gate.

Statistics (Notes):
- pass rates carry a Wilson 95% interval -- well-behaved near 0% and 100%,
  unlike the normal approximation;
- version comparison is paired on the same rows, tested with McNemar's
  exact test on the discordant pairs (fixed vs regressed);
- consistency across trials is reported as pass@k (passed at least once)
  and pass^k (passed every time).
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasets import service as datasets
from app.datasets.runtime import decision_from_output
from app.db.models import (
    AgentArtifactModel,
    BatchRunModel,
    MetamorphicRelationModel,
    SimulationRunModel,
    TwinScenarioModel,
)

_Z = 1.96


# ---- statistics -----------------------------------------------------------


class Rate(BaseModel):
    passed: int
    total: int
    rate: float | None
    low: float | None
    high: float | None


def wilson(passed: int, total: int) -> Rate:
    if total == 0:
        return Rate(passed=0, total=0, rate=None, low=None, high=None)
    p = passed / total
    denominator = 1 + _Z**2 / total
    centre = (p + _Z**2 / (2 * total)) / denominator
    half = _Z * math.sqrt(p * (1 - p) / total + _Z**2 / (4 * total**2)) / denominator
    return Rate(passed=passed, total=total, rate=round(p, 4), low=round(max(0.0, centre - half), 4), high=round(min(1.0, centre + half), 4))


def mcnemar_p(fixed: int, regressed: int) -> float:
    """Exact two-sided McNemar test: under "no real change", each
    discordant row is equally likely to be a fix or a regression."""
    n = fixed + regressed
    if n == 0:
        return 1.0
    k = min(fixed, regressed)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return round(min(1.0, 2 * tail), 4)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return round(ordered[index], 4)


def _pct(value: float) -> int:
    """Half-up, matching how the UI rounds (Python's round() is banker's)."""
    return math.floor(value * 100 + 0.5)


def _describe(rate: Rate, noun: str = "cases") -> str:
    if rate.rate is None:
        return f"No graded {noun} yet."
    return (
        f"The agent handles about {_pct(rate.rate)} in 100 {noun} correctly "
        f"(between {_pct(rate.low)} and {_pct(rate.high)}, with 95% confidence, from {rate.total} graded runs)."
    )


# ---- results --------------------------------------------------------------


class SegmentResult(BaseModel):
    segment: str
    rate: Rate
    weak: bool = False


class FailureCluster(BaseModel):
    pattern: str
    count: int
    example_reason: str
    row_keys: list[str]
    run_ids: list[str]


class Consistency(BaseModel):
    trials: int
    always_pass: int
    always_fail: int
    inconsistent: int
    pass_at_k: float | None
    pass_hat_k: float | None
    inconsistent_rows: list[str] = []


class Distribution(BaseModel):
    median: float | None
    p95: float | None
    max: float | None


class RelationResult(BaseModel):
    relation_id: str
    name: str
    kind: str
    checked: int
    held: int
    broken_rows: list[str] = []


class BatchResults(BaseModel):
    batch_id: str
    mode: str
    status: str
    headline: Rate
    reading: str
    excluded: dict[str, int]
    segments: list[SegmentResult]
    provenance: list[SegmentResult]
    decision_matrix: dict[str, dict[str, int]]
    field_accuracy: dict[str, Rate]
    clusters: list[FailureCluster]
    consistency: Consistency
    cost_usd: Distribution
    tokens: Distribution
    seconds: Distribution
    worst_rows: list[dict[str, Any]]
    relations: list[RelationResult] = []
    load: dict[str, Any] | None = None
    critical_failures: list[str] = []


def _graded(runs: list[SimulationRunModel]) -> list[SimulationRunModel]:
    """Runs that count: finished and graded, or failed to finish (an agent
    that crashes on a case got it wrong). Cancelled runs don't count."""
    return [r for r in runs if r.status != "CANCELLED" and (r.graded_passed is not None or r.status in ("FAILED", "TIMED_OUT"))]


def _passed(run: SimulationRunModel) -> bool:
    return run.graded_passed is True


_PLACEHOLDERS = [
    (re.compile(r"'[^']*'"), "'…'"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "N"),
    (re.compile(r"\s+"), " "),
]


def _pattern(reason: str) -> str:
    text = reason
    for regex, replacement in _PLACEHOLDERS:
        text = regex.sub(replacement, text)
    return text.strip()


def _duration(run: SimulationRunModel) -> float | None:
    if run.completed_at and run.started_at:
        return max(0.0, (run.completed_at - run.started_at).total_seconds())
    return None


def _distribution(values: list[float]) -> Distribution:
    return Distribution(
        median=round(statistics.median(values), 4) if values else None,
        p95=_percentile(values, 0.95),
        max=round(max(values), 4) if values else None,
    )


def batch_results(session: Session, batch: BatchRunModel) -> BatchResults:
    runs = list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.batch_id == batch.id)))
    rows = {r.row_key: r for r in datasets.rows_at(session, batch.dataset_id, batch.dataset_version)}
    current = {r.row_key: r for r in datasets.rows_at(session, batch.dataset_id)}
    # Rows marked ambiguous since the batch ran are excluded too (US35.1).
    ambiguous = {k for k, r in current.items() if r.ambiguous} | {k for k, r in rows.items() if r.ambiguous}
    scenario = session.get(TwinScenarioModel, batch.scenario_id)
    decision_field = scenario.decision_field if scenario is not None else "decision"

    excluded = Counter()
    graded = []
    for run in _graded(runs):
        if run.row_key in ambiguous:
            excluded["ambiguous"] += 1
            continue
        graded.append(run)
    excluded["cancelled"] = sum(1 for r in runs if r.status == "CANCELLED")
    excluded["not_finished"] = len(runs) - len(_graded(runs)) - excluded["cancelled"]

    headline = wilson(sum(_passed(r) for r in graded), len(graded))

    def grouped(key) -> list[SegmentResult]:
        groups: dict[str, list[SimulationRunModel]] = {}
        for run in graded:
            row = rows.get(run.row_key)
            groups.setdefault(key(row) if row else "unknown", []).append(run)
        results = []
        for name, members in sorted(groups.items()):
            rate = wilson(sum(_passed(r) for r in members), len(members))
            weak = (
                headline.rate is not None and rate.rate is not None and rate.total >= 5 and rate.rate < headline.rate - 0.10
            )
            results.append(SegmentResult(segment=name, rate=rate, weak=weak))
        return results

    matrix: dict[str, dict[str, int]] = {}
    field_hits: dict[str, list[bool]] = {}
    for run in graded:
        row = rows.get(run.row_key)
        labels = (row.labels if row else {}) or {}
        expected = labels.get("decision")
        if expected:
            actual = "escalate" if run.status == "ESCALATED" else (decision_from_output(run.final_output, decision_field) or "none")
            matrix.setdefault(expected, {}).setdefault(actual, 0)
            matrix[expected][actual] += 1
        for field_name, expected_value in (labels.get("outputs") or {}).items():
            if field_name.startswith("judgment:") or expected_value is None:
                continue
            actual_value = (run.final_output or {}).get(field_name)
            field_hits.setdefault(field_name, []).append(str(actual_value).strip().lower() == str(expected_value).strip().lower())

    clusters: dict[str, FailureCluster] = {}
    for run in graded:
        if _passed(run):
            continue
        reasons = [d.get("reason", "") for d in run.deviations or []] or [f"Run {run.status.lower()}"]
        for reason in reasons[:1]:
            pattern = _pattern(reason)
            cluster = clusters.setdefault(pattern, FailureCluster(pattern=pattern, count=0, example_reason=reason, row_keys=[], run_ids=[]))
            cluster.count += 1
            if run.row_key and run.row_key not in cluster.row_keys and len(cluster.row_keys) < 10:
                cluster.row_keys.append(run.row_key)
            if len(cluster.run_ids) < 5:
                cluster.run_ids.append(run.id)

    by_row: dict[str, list[bool]] = {}
    for run in graded:
        if run.row_key:
            by_row.setdefault(run.row_key, []).append(_passed(run))
    inconsistent_rows = sorted(k for k, v in by_row.items() if len(set(v)) > 1)
    consistency = Consistency(
        trials=batch.trials,
        always_pass=sum(1 for v in by_row.values() if all(v)),
        always_fail=sum(1 for v in by_row.values() if not any(v)),
        inconsistent=len(inconsistent_rows),
        pass_at_k=round(sum(1 for v in by_row.values() if any(v)) / len(by_row), 4) if by_row else None,
        pass_hat_k=round(sum(1 for v in by_row.values() if all(v)) / len(by_row), 4) if by_row else None,
        inconsistent_rows=inconsistent_rows[:50],
    )

    costs = [r.total_cost_usd for r in graded if r.total_cost_usd is not None]
    tokens = [float(r.total_tokens) for r in graded if r.total_tokens]
    seconds = [d for d in (_duration(r) for r in graded) if d is not None]
    worst = sorted(graded, key=lambda r: (r.total_cost_usd or 0, _duration(r) or 0), reverse=True)[:5]

    critical_failures = sorted({r.row_key for r in graded if r.row_key and rows.get(r.row_key) is not None and rows[r.row_key].critical and not _passed(r)})

    return BatchResults(
        batch_id=batch.id,
        mode=batch.mode,
        status=batch.status,
        headline=headline,
        reading=_describe(headline),
        excluded={k: v for k, v in excluded.items() if v},
        segments=grouped(lambda row: row.segment),
        provenance=grouped(lambda row: row.provenance),
        decision_matrix=matrix,
        field_accuracy={k: wilson(sum(v), len(v)) for k, v in field_hits.items()},
        clusters=sorted(clusters.values(), key=lambda c: -c.count),
        consistency=consistency,
        cost_usd=_distribution(costs),
        tokens=_distribution(tokens),
        seconds=_distribution(seconds),
        worst_rows=[
            {"run_id": r.id, "row_key": r.row_key, "cost_usd": r.total_cost_usd, "seconds": _duration(r), "passed": _passed(r)}
            for r in worst
        ],
        relations=_relations(session, batch, graded, decision_field),
        load=_load_profile(runs) if batch.mode == "load" else None,
        critical_failures=critical_failures,
    )


def _relations(session: Session, batch: BatchRunModel, graded: list[SimulationRunModel], decision_field: str) -> list[RelationResult]:
    """Epic 33, US33.5's grading: compare each variant row's decision with
    its base row's, trial for trial."""
    from app.oracles.service import relation_holds

    relations = list(session.scalars(select(MetamorphicRelationModel).where(MetamorphicRelationModel.dataset_id == batch.dataset_id)))
    if not relations:
        return []
    rows = {r.row_key: r for r in datasets.rows_at(session, batch.dataset_id, batch.dataset_version)}

    def decision(run: SimulationRunModel) -> str | None:
        return "escalate" if run.status == "ESCALATED" else decision_from_output(run.final_output, decision_field)

    by_key_trial = {(r.row_key, r.trial): r for r in graded}
    results = []
    for relation in relations:
        result = RelationResult(relation_id=relation.id, name=relation.name, kind=relation.kind, checked=0, held=0)
        for row in rows.values():
            tags = row.tags or []
            if f"relation:{relation.id}" not in tags:
                continue
            base_key = next((t.split(":", 1)[1] for t in tags if t.startswith("variant-of:")), None)
            for trial in range(1, batch.trials + 1):
                variant_run, base_run = by_key_trial.get((row.row_key, trial)), by_key_trial.get((base_key, trial))
                if variant_run is None or base_run is None:
                    continue
                holds = relation_holds(relation, decision(base_run), decision(variant_run))
                if holds is None:
                    continue
                result.checked += 1
                if holds:
                    result.held += 1
                elif row.row_key not in result.broken_rows:
                    result.broken_rows.append(row.row_key)
        results.append(result)
    return results


def _load_profile(runs: list[SimulationRunModel]) -> dict[str, Any]:
    """US34.10's report: latency and failures under concurrent load, kept
    apart from correctness."""
    durations = [d for d in (_duration(r) for r in runs) if d is not None]
    finished = [r for r in runs if r.status not in ("CREATED", "QUEUED", "RUNNING", "CANCELLED")]
    failures = [r for r in finished if r.status in ("FAILED", "TIMED_OUT")]
    starts = [r.started_at for r in runs if r.started_at]
    ends = [r.completed_at for r in runs if r.completed_at]
    wall = (max(ends) - min(starts)).total_seconds() if starts and ends else None
    return {
        "runs": len(finished),
        "p50_seconds": _percentile(durations, 0.5),
        "p95_seconds": _percentile(durations, 0.95),
        "p99_seconds": _percentile(durations, 0.99),
        "failure_rate": round(len(failures) / len(finished), 4) if finished else None,
        "throughput_per_minute": round(len(finished) / wall * 60, 2) if wall else None,
    }


# ---- paired comparison (US35.6) -------------------------------------------


class Comparison(BaseModel):
    batch_a: str
    batch_b: str
    agent_versions: tuple[int, int]
    paired_rows: int
    fixed: list[str]
    regressed: list[str]
    both_pass: int
    both_fail: int
    rate_a: Rate
    rate_b: Rate
    p_value: float
    significant: bool
    reading: str


def _row_outcomes(session: Session, batch: BatchRunModel) -> dict[str, bool]:
    """Per row: passed in every trial (a row that only sometimes passes
    counts as not passing -- consistency is the bar)."""
    outcomes: dict[str, list[bool]] = {}
    for run in _graded(list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.batch_id == batch.id)))):
        if run.row_key:
            outcomes.setdefault(run.row_key, []).append(_passed(run))
    return {k: all(v) for k, v in outcomes.items()}


def compare(session: Session, a: BatchRunModel, b: BatchRunModel) -> Comparison:
    if a.dataset_id != b.dataset_id:
        raise ValueError("Compare batches over the same dataset")
    oa, ob = _row_outcomes(session, a), _row_outcomes(session, b)
    shared = sorted(set(oa) & set(ob))
    fixed = [k for k in shared if not oa[k] and ob[k]]
    regressed = [k for k in shared if oa[k] and not ob[k]]
    p = mcnemar_p(len(fixed), len(regressed))
    rate_a = wilson(sum(oa[k] for k in shared), len(shared))
    rate_b = wilson(sum(ob[k] for k in shared), len(shared))
    significant = p < 0.05
    if not shared:
        reading = "No cases in common to compare."
    elif not fixed and not regressed:
        reading = "Both versions got exactly the same cases right."
    else:
        direction = "better" if len(fixed) > len(regressed) else "worse" if len(regressed) > len(fixed) else "different"
        reading = (
            f"v{b.agent_version} fixed {len(fixed)} case(s) and broke {len(regressed)} compared with v{a.agent_version}: "
            + (f"a real change for the {direction} (p = {p})." if significant else f"not enough to call it a real change yet (p = {p}).")
        )
    return Comparison(
        batch_a=a.id, batch_b=b.id, agent_versions=(a.agent_version, b.agent_version), paired_rows=len(shared),
        fixed=fixed, regressed=regressed, both_pass=sum(1 for k in shared if oa[k] and ob[k]),
        both_fail=sum(1 for k in shared if not oa[k] and not ob[k]), rate_a=rate_a, rate_b=rate_b,
        p_value=p, significant=significant, reading=reading,
    )


# ---- the statistical gate (US35.7) ----------------------------------------


class ReleaseCriteria(BaseModel):
    min_pass_rate_lower: float = 0.9
    min_segment_rate: float | None = 0.8
    critical_must_pass: bool = True
    no_significant_regression: bool = True
    tiers: list[str] = ["regression", "full"]


class GateResult(BaseModel):
    passed: bool
    batch_id: str | None
    reasons: list[str]
    checked_at: datetime


def gate(session: Session, artifact: AgentArtifactModel, criteria: ReleaseCriteria) -> GateResult:
    from app.ids import utcnow

    batch = session.scalar(
        select(BatchRunModel)
        .where(
            BatchRunModel.agent_artifact_id == artifact.id,
            BatchRunModel.agent_version == artifact.version,
            BatchRunModel.mode == "correctness",
            BatchRunModel.status == "completed",
            BatchRunModel.tier.in_(criteria.tiers),
        )
        .order_by(BatchRunModel.created_at.desc())
        .limit(1)
    )
    now = utcnow()
    if batch is None:
        return GateResult(
            passed=False, batch_id=None, checked_at=now,
            reasons=[f"No finished {' or '.join(criteria.tiers)} batch on agent v{artifact.version} yet"],
        )
    results = batch_results(session, batch)
    reasons = []
    if results.headline.low is None or results.headline.low < criteria.min_pass_rate_lower:
        low = "n/a" if results.headline.low is None else f"{_pct(results.headline.low)}%"
        reasons.append(f"Pass rate could be as low as {low}; the bar is {round(criteria.min_pass_rate_lower * 100)}%")
    if criteria.min_segment_rate is not None:
        for segment in results.segments:
            if segment.rate.rate is not None and segment.rate.rate < criteria.min_segment_rate:
                reasons.append(f"Segment '{segment.segment}' passes {round(segment.rate.rate * 100)}%, under {round(criteria.min_segment_rate * 100)}%")
    if criteria.critical_must_pass and results.critical_failures:
        reasons.append(f"{len(results.critical_failures)} critical case(s) failed: {', '.join(results.critical_failures[:5])}")
    if criteria.no_significant_regression:
        previous = session.scalar(
            select(BatchRunModel)
            .where(
                BatchRunModel.agent_artifact_id == artifact.id,
                BatchRunModel.agent_version < artifact.version,
                BatchRunModel.dataset_id == batch.dataset_id,
                BatchRunModel.mode == "correctness",
                BatchRunModel.status == "completed",
            )
            .order_by(BatchRunModel.agent_version.desc(), BatchRunModel.created_at.desc())
            .limit(1)
        )
        if previous is not None:
            comparison = compare(session, previous, batch)
            if comparison.significant and len(comparison.regressed) > len(comparison.fixed):
                reasons.append(f"Significantly worse than v{previous.agent_version} (p = {comparison.p_value})")
    return GateResult(passed=not reasons, batch_id=batch.id, reasons=reasons, checked_at=now)


def evidence_record(session: Session, batch: BatchRunModel, criteria: ReleaseCriteria | None) -> dict[str, Any]:
    """US35.9: one record for an audit file."""
    results = batch_results(session, batch)
    artifact = session.get(AgentArtifactModel, batch.agent_artifact_id)
    runs = list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.batch_id == batch.id).limit(5)))
    return {
        "batch_id": batch.id,
        "agent": {"id": batch.agent_artifact_id, "version": batch.agent_version, "context_pack_id": artifact.context_pack_id if artifact else None},
        "dataset": {"id": batch.dataset_id, "version": batch.dataset_version, "rows": len(batch.row_keys)},
        "tier": batch.tier,
        "trials": batch.trials,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "headline": results.headline.model_dump(),
        "reading": results.reading,
        "segments": [s.model_dump() for s in results.segments],
        "consistency": results.consistency.model_dump(),
        "critical_failures": results.critical_failures,
        "gate": gate(session, artifact, criteria).model_dump(mode="json") if artifact and criteria else None,
        "snapshot_examples": [r.snapshot for r in runs],
        "method": "Wilson 95% interval for pass rates; McNemar exact test for version comparisons.",
    }
