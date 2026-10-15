"""Epic 33: scalable oracles API -- decision rules, oracle labelling and
conflicts, label-first generation, metamorphic relations, calibrated
judges, and the review queue."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.datasets import service as datasets
from app.db.models import JudgeRubricModel, MetamorphicRelationModel, SimulationRunModel
from app.ids import new_id
from app.jobs import queue
from app.llm.routing import llm_route_for
from app.oracles import service
from app.oracles.rules import RuleView

from .deps import AgentTestDep, CurrentUserDep, DbDep, LLMDep, ProcessScope

router = APIRouter(prefix="/api/processes/{process_id}", tags=["oracles"], dependencies=[ProcessScope])


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ---- rules ----------------------------------------------------------------


@router.get("/decision-rules", response_model=list[RuleView])
def list_rules(process_id: str, db: DbDep, user: CurrentUserDep) -> list[RuleView]:
    del user
    return [service.to_view(r) for r in service.list_rules(db, process_id)]


@router.post("/decision-rules/propose", response_model=list[RuleView])
async def propose_rules(process_id: str, db: DbDep, llm: LLMDep, user: AgentTestDep) -> list[RuleView]:
    """US33.1: draft rules for decision points that have none yet."""
    del user
    try:
        with llm_route_for(db, process_id=process_id):
            await service.propose_rules(db, llm, process_id)
    except service.OracleError as exc:
        raise _bad(exc) from exc
    return [service.to_view(r) for r in service.list_rules(db, process_id)]


@router.patch("/decision-rules/{rule_id}", response_model=RuleView)
def update_rule(process_id: str, rule_id: str, body: service.RuleUpdate, db: DbDep, user: AgentTestDep) -> RuleView:
    try:
        return service.to_view(service.update_rule(db, process_id, rule_id, body, user_id=user.id))
    except service.OracleError as exc:
        raise _bad(exc) from exc


# ---- oracle on datasets ---------------------------------------------------


@router.post("/datasets/{dataset_id}/oracle/label", response_model=service.OracleLabelResult)
def oracle_label(process_id: str, dataset_id: str, db: DbDep, user: AgentTestDep) -> service.OracleLabelResult:
    """US33.2."""
    dataset = datasets.get_dataset_model(db, process_id, dataset_id)
    try:
        return service.label_with_oracle(db, dataset, user_id=user.id)
    except service.OracleError as exc:
        raise _bad(exc) from exc


@router.get("/datasets/{dataset_id}/oracle/conflicts", response_model=list[service.OracleConflict])
def oracle_conflicts(process_id: str, dataset_id: str, db: DbDep, user: CurrentUserDep) -> list[service.OracleConflict]:
    """US33.3."""
    del user
    return service.oracle_conflicts(db, datasets.get_dataset_model(db, process_id, dataset_id))


class GapReport(BaseModel):
    row_keys: list[str]


@router.post("/datasets/{dataset_id}/oracle/conflicts/report-gap")
def report_gap(process_id: str, dataset_id: str, body: GapReport, db: DbDep, user: AgentTestDep) -> dict[str, str]:
    """US33.3: "the rules don't match practice" becomes a process gap."""
    del user
    try:
        return {"gap_finding_id": service.report_rules_gap(db, datasets.get_dataset_model(db, process_id, dataset_id), body.row_keys)}
    except service.OracleError as exc:
        raise _bad(exc) from exc


class GenerateStarted(BaseModel):
    queued: bool
    job_id: str | None = None
    result: service.GenerateResult | None = None


@router.post("/datasets/{dataset_id}/generate", response_model=GenerateStarted)
async def generate(
    process_id: str, dataset_id: str, body: service.GenerateRequest, db: DbDep, llm: LLMDep, user: AgentTestDep
) -> GenerateStarted:
    """US33.4: label-first generation. Queued when the job worker runs
    (large sets take many LLM calls), otherwise done in this request."""
    dataset = datasets.get_dataset_model(db, process_id, dataset_id)
    try:
        service.require_confirmed_rules(service.confirmed_views(db, process_id))
    except service.OracleError as exc:
        raise _bad(exc) from exc
    if get_settings().job_worker_enabled:
        job = queue.enqueue(
            db, "label_first_generate", {"process_id": process_id, "dataset_id": dataset.id, "request": body.model_dump(), "user_id": user.id},
            max_attempts=1,
        )
        db.commit()
        return GenerateStarted(queued=True, job_id=job.id)
    try:
        with llm_route_for(db, process_id=process_id):
            result = await service.generate_label_first(db, llm, dataset, body, user_id=user.id)
    except service.OracleError as exc:
        raise _bad(exc) from exc
    return GenerateStarted(queued=False, result=result)


# ---- metamorphic relations ------------------------------------------------


class RelationView(service.RelationCreate):
    id: str
    dataset_id: str


def _relation_view(r: MetamorphicRelationModel) -> RelationView:
    return RelationView(id=r.id, dataset_id=r.dataset_id, name=r.name, kind=r.kind, field=r.field, perturbation=r.perturbation, direction=r.direction)


@router.get("/datasets/{dataset_id}/relations", response_model=list[RelationView])
def list_relations(process_id: str, dataset_id: str, db: DbDep, user: CurrentUserDep) -> list[RelationView]:
    del user
    dataset = datasets.get_dataset_model(db, process_id, dataset_id)
    rows = db.scalars(select(MetamorphicRelationModel).where(MetamorphicRelationModel.dataset_id == dataset.id))
    return [_relation_view(r) for r in rows]


@router.post("/datasets/{dataset_id}/relations", response_model=RelationView, status_code=status.HTTP_201_CREATED)
def create_relation(process_id: str, dataset_id: str, body: service.RelationCreate, db: DbDep, user: AgentTestDep) -> RelationView:
    """US33.5."""
    del user
    try:
        return _relation_view(service.create_relation(db, datasets.get_dataset_model(db, process_id, dataset_id), body))
    except service.OracleError as exc:
        raise _bad(exc) from exc


@router.post("/datasets/{dataset_id}/relations/{relation_id}/variants")
def generate_variants(process_id: str, dataset_id: str, relation_id: str, db: DbDep, user: AgentTestDep) -> dict[str, int]:
    dataset = datasets.get_dataset_model(db, process_id, dataset_id)
    relation = db.get(MetamorphicRelationModel, relation_id)
    if relation is None or relation.dataset_id != dataset.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"relation '{relation_id}' not found")
    return {"variants": service.generate_variants(db, dataset, relation, user_id=user.id), "version": dataset.version}


# ---- judges ---------------------------------------------------------------


class RubricView(service.RubricCreate):
    id: str
    version: int
    calibration: dict | None = None
    gate_eligible: bool = False


def _rubric_view(r: JudgeRubricModel) -> RubricView:
    return RubricView(
        id=r.id, name=r.name, field=r.field, rubric=r.rubric, judge_model=r.judge_model, min_agreement=r.min_agreement,
        min_samples=r.min_samples, version=r.version, calibration=r.calibration, gate_eligible=service.gate_eligible(r),
    )


def _get_rubric(db, process_id: str, rubric_id: str) -> JudgeRubricModel:
    rubric = db.get(JudgeRubricModel, rubric_id)
    if rubric is None or rubric.process_id != process_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"rubric '{rubric_id}' not found")
    return rubric


@router.get("/judge-rubrics", response_model=list[RubricView])
def list_rubrics(process_id: str, db: DbDep, user: CurrentUserDep) -> list[RubricView]:
    del user
    return [_rubric_view(r) for r in db.scalars(select(JudgeRubricModel).where(JudgeRubricModel.process_id == process_id))]


@router.post("/judge-rubrics", response_model=RubricView, status_code=status.HTTP_201_CREATED)
def create_rubric(process_id: str, body: service.RubricCreate, db: DbDep, user: AgentTestDep) -> RubricView:
    del user
    rubric = JudgeRubricModel(id=new_id("rubric"), process_id=process_id, **body.model_dump())
    db.add(rubric)
    db.flush()
    return _rubric_view(rubric)


@router.put("/judge-rubrics/{rubric_id}", response_model=RubricView)
def update_rubric(process_id: str, rubric_id: str, body: service.RubricCreate, db: DbDep, user: AgentTestDep) -> RubricView:
    """Changing the rubric or judge model voids the calibration."""
    del user
    rubric = _get_rubric(db, process_id, rubric_id)
    if body.rubric != rubric.rubric or body.judge_model != rubric.judge_model or body.field != rubric.field:
        rubric.version += 1
    for key, value in body.model_dump().items():
        setattr(rubric, key, value)
    db.flush()
    return _rubric_view(rubric)


@router.post("/judge-rubrics/{rubric_id}/calibrate/{dataset_id}", response_model=RubricView)
async def calibrate(process_id: str, rubric_id: str, dataset_id: str, db: DbDep, llm: LLMDep, user: AgentTestDep) -> RubricView:
    """US33.6."""
    del user
    rubric = _get_rubric(db, process_id, rubric_id)
    try:
        with llm_route_for(db, process_id=process_id):
            await service.calibrate(db, llm, rubric, datasets.get_dataset_model(db, process_id, dataset_id))
    except service.OracleError as exc:
        raise _bad(exc) from exc
    return _rubric_view(rubric)


@router.post("/judge-rubrics/{rubric_id}/judge-run/{run_id}", response_model=service.JudgeVerdict)
async def judge_run(process_id: str, rubric_id: str, run_id: str, db: DbDep, llm: LLMDep, user: AgentTestDep) -> service.JudgeVerdict:
    del user
    rubric = _get_rubric(db, process_id, rubric_id)
    run = db.get(SimulationRunModel, run_id)
    if run is None or run.process_id != process_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"run '{run_id}' not found")
    output = (run.final_output or {}).get(rubric.field)
    if output is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"The run's output has no '{rubric.field}'")
    try:
        with llm_route_for(db, process_id=process_id):
            return await service.judge(llm, rubric, str(output), {})
    except service.OracleError as exc:
        raise _bad(exc) from exc


# ---- review queue ---------------------------------------------------------


@router.get("/datasets/{dataset_id}/review-queue", response_model=list[service.ReviewItem])
def review_queue(process_id: str, dataset_id: str, db: DbDep, user: CurrentUserDep) -> list[service.ReviewItem]:
    """US33.7."""
    del user
    return service.review_queue(db, datasets.get_dataset_model(db, process_id, dataset_id))
