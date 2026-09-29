"""Epic 19: regression suites (US19.2/US19.3) and run comparison (US19.4).
Snapshot/reproducibility (Epic 22) also lands here once built, same
"grouped by cross-cutting concern, not by which epic first touched a run"
convention app/api/twin.py/app/api/orchestration.py already follow for
scenario/run endpoints.
"""

from fastapi import APIRouter, BackgroundTasks, status

from app.db import repository
from app.schemas.evaluation import RegressionSuite, RegressionSuiteCreate, RegressionSuiteRun, RunComparison

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}", tags=["evaluation"])


@router.post("/regression-suites", response_model=RegressionSuite, status_code=status.HTTP_201_CREATED)
def create_regression_suite(
    process_id: str, body: RegressionSuiteCreate, db: DbDep, user: EditorDep
) -> RegressionSuite:
    return repository.create_regression_suite(db, process_id, body, created_by=user.id)


@router.get("/regression-suites", response_model=list[RegressionSuite])
def list_regression_suites(process_id: str, db: DbDep, user: CurrentUserDep) -> list[RegressionSuite]:
    del user
    return repository.list_regression_suites(db, process_id)


@router.delete("/regression-suites/{suite_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_regression_suite(process_id: str, suite_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.delete_regression_suite(db, process_id, suite_id)


@router.post(
    "/regression-suites/{suite_id}/run", response_model=RegressionSuiteRun, status_code=status.HTTP_202_ACCEPTED
)
def start_regression_suite_run(
    process_id: str, suite_id: str, db: DbDep, llm: LLMDep, user: EditorDep, background_tasks: BackgroundTasks
) -> RegressionSuiteRun:
    """US19.3: same two-step create-then-enqueue handoff as
    app/api/twin.py's/app/api/orchestration.py's `start_run` -- one
    background task per scenario in the suite, run kind picked from the
    suite's own declared kind."""
    suite_run, run_ids, kind = repository.start_regression_suite_run(db, process_id, suite_id, run_by=user.id)
    db.commit()
    for run_id in run_ids:
        if kind == "twin":
            background_tasks.add_task(repository.execute_twin_run_background, run_id, llm)
        else:
            background_tasks.add_task(repository.execute_orchestration_run_background, run_id, llm)
    return suite_run


@router.get("/regression-suite-runs/{suite_run_id}", response_model=RegressionSuiteRun)
def get_regression_suite_run(process_id: str, suite_run_id: str, db: DbDep, user: CurrentUserDep) -> RegressionSuiteRun:
    del user
    return repository.get_regression_suite_run(db, process_id, suite_run_id)


@router.get("/runs/compare", response_model=RunComparison)
def compare_runs(process_id: str, run_a: str, run_b: str, db: DbDep, user: CurrentUserDep) -> RunComparison:
    del user
    return repository.compare_runs(db, process_id, run_a, run_b)
