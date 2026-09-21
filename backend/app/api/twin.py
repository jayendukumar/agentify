from fastapi import APIRouter, HTTPException, status

from app.db import repository
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


@router.post("/scenarios/{scenario_id}/run", response_model=TwinRun)
async def run_scenario(process_id: str, scenario_id: str, db: DbDep, llm: LLMDep, user: EditorDep) -> TwinRun:
    try:
        return await repository.execute_twin_run(llm, db, process_id, scenario_id, run_by=user.id)
    except TwinServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


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
