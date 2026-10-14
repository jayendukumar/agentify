"""Epic 36, US36.4/US36.5/US36.7: operating models, process twin batches,
and their efficiency report.

A process twin batch runs one orchestration scenario over a dataset's
cases: one `kind="orchestration"` SimulationRunModel per case, carrying the
row key, the pinned dataset version and a per-case seed, executed by the
`process_twin_run` job (app/process_twin/jobs.py). The orchestration
executor reads the row's inputs and the seed off the run row, so a case
can be replayed alone. The batch snapshots the operating model it ran
with, so a later edit doesn't rewrite an old report.
"""

from __future__ import annotations

import hashlib
import statistics
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.batches.service import select_rows
from app.bpmn.nodes import extract_flow_nodes
from app.datasets import service as datasets
from app.db import repository
from app.db.models import (
    OrchestrationScenarioModel,
    ProcessOperatingModel,
    ProcessTwinBatchModel,
    SimulationRunModel,
)
from app.ids import new_id, utcnow
from app.jobs import queue
from app.schemas.process_twin import (
    EfficiencyReport,
    NodeOperatingModel,
    OperatingModel,
    OperatingModelView,
    ProcessTwinBatchView,
    ProcessTwinEstimate,
    ProcessTwinRequest,
)
from app.schemas.run import NON_TERMINAL_RUN_STATUSES
from app.store import NotFoundError

from .efficiency import CaseInput, build_report

_TASK_TYPES = {"task", "userTask", "serviceTask"}
_DEFAULT_CASE_COST_USD = 0.02
JOB_KIND = "process_twin_run"


class ProcessTwinError(Exception):
    pass


def _now():
    return utcnow().replace(tzinfo=None)


# ---- the operating model (US36.4) -----------------------------------------


def _flow_nodes(session: Session, process_id: str):
    overlay = repository.get_blueprint_overlay(session, process_id)
    if overlay is None:
        raise ProcessTwinError("The process has no blueprint yet -- finalize it and generate the blueprint first")
    return extract_flow_nodes(repository.get_version(session, process_id, overlay.baseline_version_id).xml)


def default_operating_model(session: Session, process_id: str) -> OperatingModel:
    """Pre-filled from the finalized diagram: one row per task with its lane
    as the role; times left empty for the process owner to fill in. Every
    end event maps to its own label until told otherwise."""
    nodes = _flow_nodes(session, process_id)
    roles = sorted({n.lane_name for n in nodes if n.bpmn_type in _TASK_TYPES and n.lane_name})
    return OperatingModel(
        roles=[{"role": r, "hourly_cost": 0} for r in roles],
        nodes=[NodeOperatingModel(node_id=n.id, label=n.label, role=n.lane_name or "")
               for n in nodes if n.bpmn_type in _TASK_TYPES],
        end_event_outcomes={n.id: n.label for n in nodes if n.bpmn_type == "endEvent"},
    )


def get_operating_model(session: Session, process_id: str) -> OperatingModelView:
    repository.get_process(session, process_id)  # 404s if missing
    row = session.get(ProcessOperatingModel, process_id)
    if row is None:
        return OperatingModelView(
            **default_operating_model(session, process_id).model_dump(), process_id=process_id, version=0, is_default=True
        )
    return OperatingModelView(**row.model, process_id=process_id, version=row.version, updated_at=row.updated_at)


def save_operating_model(session: Session, process_id: str, model: OperatingModel, *, user_id: str | None) -> OperatingModelView:
    repository.get_process(session, process_id)
    row = session.get(ProcessOperatingModel, process_id)
    if row is None:
        row = ProcessOperatingModel(process_id=process_id, model=model.model_dump(mode="json"), version=1, updated_by=user_id)
        session.add(row)
    else:
        row.model = model.model_dump(mode="json")
        row.version += 1
        row.updated_by = user_id
    session.flush()
    return get_operating_model(session, process_id)


# ---- batches (US36.5) -----------------------------------------------------


def _scenario(session: Session, process_id: str, scenario_id: str) -> OrchestrationScenarioModel:
    scenario = session.get(OrchestrationScenarioModel, scenario_id)
    if scenario is None or scenario.process_id != process_id:
        raise NotFoundError("orchestration scenario", scenario_id)
    manual = [node for node, cfg in (scenario.human_checkpoint_config or {}).items() if cfg.get("mode") == "manual"]
    manual += [node for node, cfg in (scenario.manual_node_config or {}).items()
               if (cfg.get("human_checkpoint_config") or {}).get("mode") == "manual"]
    if manual:
        raise ProcessTwinError(
            f"Steps {sorted(manual)} wait for a real person's decision -- a process twin runs unattended, so give "
            "them a simulated reviewer (probability or rule) first"
        )
    return scenario


def _rows(session: Session, process_id: str, request: ProcessTwinRequest):
    dataset = datasets.get_dataset_model(session, process_id, request.dataset_id)
    rows = [r for r in datasets.rows_at(session, dataset.id) if not r.ambiguous]
    chosen = select_rows(rows, tier="custom" if request.sample_size else "full", size=request.sample_size,
                         fraction=None, seed=request.seed)
    if not chosen:
        raise ProcessTwinError("The dataset has no usable cases (it's empty, or every case is marked ambiguous)")
    return dataset, chosen


def estimate(session: Session, process_id: str, request: ProcessTwinRequest) -> ProcessTwinEstimate:
    _scenario(session, process_id, request.scenario_id)
    _, chosen = _rows(session, process_id, request)
    recent = list(session.scalars(
        select(SimulationRunModel.total_cost_usd)
        .where(SimulationRunModel.scenario_id == request.scenario_id, SimulationRunModel.status == "COMPLETED",
               SimulationRunModel.total_cost_usd.is_not(None))
        .order_by(SimulationRunModel.started_at.desc()).limit(30)
    ))
    per_case = statistics.fmean(recent) if recent else _DEFAULT_CASE_COST_USD
    return ProcessTwinEstimate(
        cases=len(chosen), cost_per_case_usd=round(per_case, 5), total_cost_usd=round(per_case * len(chosen), 4),
        basis=f"average of this scenario's last {len(recent)} finished runs" if recent else "no runs yet -- a default guess",
    )


def case_seed(batch_id: str, row_key: str) -> int:
    return int(hashlib.sha256(f"{batch_id}:{row_key}".encode()).hexdigest()[:8], 16)


def start(session: Session, process_id: str, request: ProcessTwinRequest, *, created_by: str | None) -> ProcessTwinBatchModel:
    scenario = _scenario(session, process_id, request.scenario_id)
    dataset, chosen = _rows(session, process_id, request)
    model = get_operating_model(session, process_id)
    batch = ProcessTwinBatchModel(
        id=new_id("ptwin"),
        process_id=process_id,
        name=request.name or f"{scenario.name} x {dataset.name}",
        scenario_id=scenario.id,
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        row_keys=[r.row_key for r in chosen],
        status="running",
        budget_usd=request.budget_usd,
        seed=request.seed,
        operating_model=OperatingModel.model_validate(model.model_dump()).model_dump(mode="json"),
        operating_model_version=model.version,
        created_by=created_by,
    )
    session.add(batch)
    session.flush()
    for row in chosen:
        run = SimulationRunModel(
            id=new_id("simrun"), kind="orchestration", scenario_id=scenario.id, process_id=process_id,
            status="CREATED", dataset_id=dataset.id, dataset_version=dataset.version, row_key=row.row_key,
            trial=1, batch_id=batch.id, seed=case_seed(batch.id, row.row_key), run_by=created_by, queued_at=_now(),
        )
        session.add(run)
        session.flush()
        queue.enqueue(session, JOB_KIND, {"run_id": run.id}, max_attempts=1)
    session.flush()
    return batch


def get_batch(session: Session, process_id: str, batch_id: str) -> ProcessTwinBatchModel:
    batch = session.get(ProcessTwinBatchModel, batch_id)
    if batch is None or batch.process_id != process_id:
        raise NotFoundError("process twin batch", batch_id)
    return batch


def batch_runs(session: Session, batch_id: str) -> list[SimulationRunModel]:
    return list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.batch_id == batch_id)))


def to_view(session: Session, batch: ProcessTwinBatchModel) -> ProcessTwinBatchView:
    runs = batch_runs(session, batch.id)
    return ProcessTwinBatchView(
        id=batch.id, process_id=batch.process_id, name=batch.name, scenario_id=batch.scenario_id,
        dataset_id=batch.dataset_id, dataset_version=batch.dataset_version, status=batch.status,
        budget_usd=batch.budget_usd, seed=batch.seed, cases=len(runs),
        finished=sum(r.status not in NON_TERMINAL_RUN_STATUSES for r in runs),
        spent_usd=round(sum(r.total_cost_usd or 0 for r in runs), 5),
        operating_model_version=batch.operating_model_version, created_at=batch.created_at, finished_at=batch.finished_at,
    )


def list_batches(session: Session, process_id: str) -> list[ProcessTwinBatchView]:
    query = (select(ProcessTwinBatchModel).where(ProcessTwinBatchModel.process_id == process_id)
             .order_by(ProcessTwinBatchModel.created_at.desc()).limit(50))
    return [to_view(session, b) for b in session.scalars(query)]


def _cancel_pending(session: Session, batch: ProcessTwinBatchModel, reason: str) -> None:
    for run in batch_runs(session, batch.id):
        if run.status == "CREATED":
            run.status = "CANCELLED"
            run.deviations = [{"reason": reason}]
            run.completed_at = _now()
    session.flush()


def set_status(session: Session, batch: ProcessTwinBatchModel, action: Literal["pause", "resume", "cancel"]) -> None:
    if batch.status in ("completed", "cancelled", "stopped_budget"):
        raise ProcessTwinError(f"The run is already {batch.status.replace('_', ' ')}")
    if action == "pause":
        batch.status = "paused"
    elif action == "resume":
        batch.status = "running"
    else:
        batch.status = "cancelled"
        _cancel_pending(session, batch, "Process twin run was cancelled")
        finish_if_done(session, batch.id)
    session.flush()


def gate_run(session: Session, run: SimulationRunModel) -> Literal["go", "wait", "skip"]:
    """Checked before a case runs: pause waits; cancel and the budget cap skip."""
    batch = session.get(ProcessTwinBatchModel, run.batch_id)
    if batch is None or run.status != "CREATED":
        return "skip" if run.status != "CREATED" else "go"
    if batch.status == "paused":
        return "wait"
    if batch.status in ("cancelled", "stopped_budget", "completed"):
        run.status = "CANCELLED"
        run.deviations = [{"reason": f"Process twin run {batch.status.replace('_', ' ')}"}]
        run.completed_at = _now()
        session.flush()
        return "skip"
    if batch.budget_usd is not None and sum(r.total_cost_usd or 0 for r in batch_runs(session, batch.id)) >= batch.budget_usd:
        batch.status = "stopped_budget"
        _cancel_pending(session, batch, f"Budget of ${batch.budget_usd:.2f} reached")
        return "skip"
    return "go"


def finish_if_done(session: Session, batch_id: str | None) -> None:
    """Once no case is in flight, the batch is done and its report is
    computed and kept (a later operating model edit doesn't change it)."""
    batch = session.get(ProcessTwinBatchModel, batch_id) if batch_id else None
    if batch is None:
        return
    if any(r.status in NON_TERMINAL_RUN_STATUSES for r in batch_runs(session, batch.id)):
        return
    if batch.status in ("running", "paused"):
        batch.status = "completed"
    if batch.finished_at is None:
        batch.finished_at = _now()
    batch.results = compute_report(session, batch).model_dump(mode="json")
    session.flush()


# ---- the report (US36.6/US36.7) -------------------------------------------


def compute_report(session: Session, batch: ProcessTwinBatchModel) -> EfficiencyReport:
    rows = {r.row_key: r for r in datasets.rows_at(session, batch.dataset_id, batch.dataset_version)}
    runs = [r for r in batch_runs(session, batch.id) if r.status not in NON_TERMINAL_RUN_STATUSES and r.status != "CANCELLED"]
    cases = [
        CaseInput(
            run_id=r.id, row_key=r.row_key, status=r.status, node_runs=r.node_runs or [], steps=r.steps or [],
            visited_path=r.visited_path or [], total_cost_usd=r.total_cost_usd,
            labels=(rows[r.row_key].labels if r.row_key in rows else None), seed=r.seed or 0,
            deviations=r.deviations or [],
            segment=rows[r.row_key].segment if r.row_key in rows else "typical",
        )
        for r in sorted(runs, key=lambda r: r.row_key or "")
    ]
    flow = _flow_nodes(session, batch.process_id)
    labels_by_node = {n.id: (n.label, n.bpmn_type) for n in flow}
    model = OperatingModel.model_validate(batch.operating_model)
    return build_report(cases, model, labels_by_node, model_version=batch.operating_model_version)


def report(session: Session, batch: ProcessTwinBatchModel) -> EfficiencyReport:
    if batch.results is not None and batch.status not in ("running", "paused"):
        return EfficiencyReport.model_validate(batch.results)
    return compute_report(session, batch)
