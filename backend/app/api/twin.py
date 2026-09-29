from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.db import repository
from app.schemas.run import ResumeDecision
from app.schemas.twin import TwinBaseline, TwinBaselineInput, TwinRun, TwinScenario, TwinScenarioCreate, TwinSummary
from app.twin.errors import TwinServiceError

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}", tags=["twin"])


@router.post("/agent-artifacts/{artifact_id}/scenarios", response_model=TwinScenario)
def create_scenario(
    process_id: str, artifact_id: str, body: TwinScenarioCreate, db: DbDep, user: EditorDep
) -> TwinScenario:
    return repository.create_twin_scenario(db, process_id, artifact_id, body, created_by=user.id)


@router.get("/agent-artifacts/{artifact_id}/scenarios", response_model=list[TwinScenario])
def list_scenarios(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> list[TwinScenario]:
    del user
    return repository.list_twin_scenarios(db, process_id, artifact_id)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(process_id: str, scenario_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.delete_twin_scenario(db, process_id, scenario_id)


@router.post("/scenarios/{scenario_id}/run", response_model=TwinRun, status_code=status.HTTP_202_ACCEPTED)
def start_run(
    process_id: str, scenario_id: str, db: DbDep, llm: LLMDep, user: EditorDep, background_tasks: BackgroundTasks
) -> TwinRun:
    """Epic 17, US17.6: same two-step BackgroundTask handoff
    app/api/orchestration.py's `start_run` already uses -- a twin run used
    to execute synchronously inside this one request; now it's created in
    status="CREATED" and driven to completion by a background task, so it
    can be polled (`get_run`), cancelled, and (via a manual human
    checkpoint) suspended/resumed exactly like an orchestration run."""
    run = repository.start_twin_run(db, process_id, scenario_id, run_by=user.id)
    db.commit()
    background_tasks.add_task(repository.execute_twin_run_background, run.id, llm)
    return run


@router.get("/twin-runs/{run_id}", response_model=TwinRun)
def get_run(process_id: str, run_id: str, db: DbDep, user: CurrentUserDep) -> TwinRun:
    del user
    return repository.get_twin_run(db, process_id, run_id)


@router.post("/twin-runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(process_id: str, run_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.cancel_run(db, process_id, run_id, kind="twin")


@router.post("/twin-runs/{run_id}/resume", response_model=TwinRun, status_code=status.HTTP_202_ACCEPTED)
def resume_run(
    process_id: str, run_id: str, body: ResumeDecision, db: DbDep, llm: LLMDep, user: EditorDep,
    background_tasks: BackgroundTasks,
) -> TwinRun:
    del user
    try:
        run = repository.resume_run(db, process_id, run_id, body.decision, kind="twin")
    except TwinServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    background_tasks.add_task(repository.execute_twin_run_background, run.id, llm, human_decision=body.decision)
    return repository.get_twin_run(db, process_id, run_id)


@router.get("/agent-artifacts/{artifact_id}/runs", response_model=list[TwinRun])
def list_runs(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> list[TwinRun]:
    del user
    return repository.list_twin_runs(db, process_id, artifact_id)


@router.get("/agent-artifacts/{artifact_id}/twin-summary", response_model=TwinSummary)
def get_twin_summary(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> TwinSummary:
    del user
    return repository.get_twin_summary(db, process_id, artifact_id)


@router.put("/agent-artifacts/{artifact_id}/baseline", response_model=TwinBaseline)
def set_baseline(
    process_id: str, artifact_id: str, body: TwinBaselineInput, db: DbDep, user: EditorDep
) -> TwinBaseline:
    return repository.set_twin_baseline(db, process_id, artifact_id, body, recorded_by=user.id)


@router.delete("/agent-artifacts/{artifact_id}/baseline", status_code=status.HTTP_204_NO_CONTENT)
def delete_baseline(process_id: str, artifact_id: str, db: DbDep, user: EditorDep) -> None:
    repository.delete_twin_baseline(db, process_id, artifact_id)
