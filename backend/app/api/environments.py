"""Epic 21: named, versioned Digital Twin Environments and their
fault-injection catalog -- see planning/epics/21-twin-environments-and-
fault-injection.md.
"""

from fastapi import APIRouter, status

from app.db import repository
from app.schemas.environment import TwinEnvironment, TwinEnvironmentCreate, TwinEnvironmentUpdate

from .deps import CurrentUserDep, DbDep, EditorDep

router = APIRouter(prefix="/api/processes/{process_id}/twin-environments", tags=["environments"])


@router.post("", response_model=TwinEnvironment, status_code=status.HTTP_201_CREATED)
def create_environment(process_id: str, body: TwinEnvironmentCreate, db: DbDep, user: EditorDep) -> TwinEnvironment:
    return repository.create_twin_environment(db, process_id, body, created_by=user.id)


@router.get("", response_model=list[TwinEnvironment])
def list_environments(process_id: str, db: DbDep, user: CurrentUserDep) -> list[TwinEnvironment]:
    del user
    return repository.list_twin_environments(db, process_id)


@router.put("/{environment_id}", response_model=TwinEnvironment)
def update_environment(
    process_id: str, environment_id: str, body: TwinEnvironmentUpdate, db: DbDep, user: EditorDep
) -> TwinEnvironment:
    del user
    return repository.update_twin_environment(db, process_id, environment_id, body)


@router.delete("/{environment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_environment(process_id: str, environment_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.delete_twin_environment(db, process_id, environment_id)
