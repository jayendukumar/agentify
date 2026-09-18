from fastapi import APIRouter, status

from app.db import repository
from app.db.models import ProcessModel
from app.schemas.processes import ProcessCreateRequest, ProcessDetail, ProcessSummary

from .deps import DbDep, StoreDep

router = APIRouter(prefix="/api/processes", tags=["processes"])


def _to_summary(process: ProcessModel, store: StoreDep) -> ProcessSummary:
    return ProcessSummary(
        id=process.id,
        name=process.name,
        document_count=len(process.documents),
        has_draft_bpmn=process.draft_bpmn is not None,  # US3.3's own table now, not the in-memory store
        finalized_version_count=len(store.list_versions(process.id)),
        created_at=process.created_at,
        updated_at=process.updated_at,
    )


@router.post("", response_model=ProcessSummary, status_code=status.HTTP_201_CREATED)
def create_process(body: ProcessCreateRequest, db: DbDep, store: StoreDep) -> ProcessSummary:
    process = repository.create_process(db, name=body.name)
    return _to_summary(process, store)


@router.get("", response_model=list[ProcessSummary])
def list_processes(db: DbDep, store: StoreDep) -> list[ProcessSummary]:
    return [_to_summary(p, store) for p in repository.list_processes(db)]


@router.get("/{process_id}", response_model=ProcessDetail)
def get_process(process_id: str, db: DbDep, store: StoreDep) -> ProcessDetail:
    process = repository.get_process(db, process_id)
    schema = repository.get_process_schema(db, process_id)
    return ProcessDetail(**_to_summary(process, store).model_dump(), process_schema=schema)


@router.delete("/{process_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_process(process_id: str, db: DbDep) -> None:
    repository.delete_process(db, process_id)
