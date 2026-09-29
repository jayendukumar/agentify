"""Epic 16: orchestration rehearsal scenarios/runs -- see
planning/epics/16-orchestration-rehearsal.md.

The run endpoint follows the exact same two-step BackgroundTask handoff as
app/api/documents.py's upload endpoint: create the row, commit explicitly
(a BackgroundTask can start running before DbDep's own end-of-request
commit), then enqueue the background execution."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.db import repository
from app.schemas.orchestration import OrchestrationRun, OrchestrationScenario, OrchestrationScenarioCreate
from app.schemas.run import ResumeDecision
from app.twin.errors import TwinServiceError

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}/orchestration", tags=["orchestration"])


@router.post("/scenarios", response_model=OrchestrationScenario, status_code=status.HTTP_201_CREATED)
def create_scenario(
    process_id: str, body: OrchestrationScenarioCreate, db: DbDep, user: EditorDep
) -> OrchestrationScenario:
    return repository.create_orchestration_scenario(db, process_id, body, created_by=user.id)


@router.get("/scenarios", response_model=list[OrchestrationScenario])
def list_scenarios(process_id: str, db: DbDep, user: CurrentUserDep) -> list[OrchestrationScenario]:
    del user
    return repository.list_orchestration_scenarios(db, process_id)


@router.delete("/scenarios/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_scenario(process_id: str, scenario_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.delete_orchestration_scenario(db, process_id, scenario_id)


@router.post("/scenarios/{scenario_id}/run", response_model=OrchestrationRun, status_code=status.HTTP_202_ACCEPTED)
def start_run(
    process_id: str, scenario_id: str, db: DbDep, llm: LLMDep, user: EditorDep, background_tasks: BackgroundTasks
) -> OrchestrationRun:
    run = repository.start_orchestration_run(db, process_id, scenario_id, run_by=user.id)
    db.commit()
    background_tasks.add_task(repository.execute_orchestration_run_background, run.id, llm)
    return run


@router.get("/runs/{run_id}", response_model=OrchestrationRun)
def get_run(process_id: str, run_id: str, db: DbDep, user: CurrentUserDep) -> OrchestrationRun:
    del user
    return repository.get_orchestration_run(db, process_id, run_id)


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(process_id: str, run_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.cancel_run(db, process_id, run_id, kind="orchestration")


@router.post("/runs/{run_id}/resume", response_model=OrchestrationRun, status_code=status.HTTP_202_ACCEPTED)
def resume_run(
    process_id: str, run_id: str, body: ResumeDecision, db: DbDep, llm: LLMDep, user: EditorDep,
    background_tasks: BackgroundTasks,
) -> OrchestrationRun:
    del user
    try:
        run = repository.resume_run(db, process_id, run_id, body.decision, kind="orchestration")
    except TwinServiceError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    background_tasks.add_task(repository.execute_orchestration_run_background, run.id, llm, human_decision=body.decision)
    return repository.get_orchestration_run(db, process_id, run_id)


@router.get("/runs", response_model=list[OrchestrationRun])
def list_runs(process_id: str, db: DbDep, user: CurrentUserDep, scenario_id: str | None = None) -> list[OrchestrationRun]:
    del user
    return repository.list_orchestration_runs(db, process_id, scenario_id)
