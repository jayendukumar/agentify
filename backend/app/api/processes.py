from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.permissions import Permission, may_act_in_unit
from app.context import repository as context_repository
from app.db import repository
from app.db.models import ProcessModel
from app.schemas.context import ProcessScope
from app.schemas.processes import ProcessCreateRequest, ProcessDetail, ProcessSummary

from .deps import CurrentUserDep, DbDep, ProcessDeleteDep, ProcessEditDep

router = APIRouter(prefix="/api/processes", tags=["processes"])


def _to_summary(process: ProcessModel, db: Session) -> ProcessSummary:
    return ProcessSummary(
        id=process.id,
        name=process.name,
        document_count=len(process.documents),
        has_draft_bpmn=process.draft_bpmn is not None,  # US3.3's own table now, not the in-memory store
        finalized_version_count=len(repository.list_versions(db, process.id)),  # US6.1's own table now
        created_at=process.created_at,
        updated_at=process.updated_at,
        scope=ProcessScope(
            business_unit_id=process.business_unit_id,
            jurisdictions=list(process.jurisdictions or []),
            process_type=process.process_type,
            data_classes=list(process.data_classes or []),
        ),
    )


@router.post("", response_model=ProcessSummary, status_code=status.HTTP_201_CREATED)
def create_process(body: ProcessCreateRequest, db: DbDep, request: Request, user: ProcessEditDep) -> ProcessSummary:
    # Epic 37, US37.2: a role scoped to a business unit creates processes in
    # that unit only.
    unit = body.scope.business_unit_id if body.scope is not None else None
    if not may_act_in_unit(user, Permission.PROCESS_EDIT, unit):
        reason = "Your role is scoped to a business unit -- create the process in that unit"
        audit.record_refusal(actor=user, action="create_process", reason=reason, request=request,
                             detail={"business_unit_id": unit})
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=reason)
    process = repository.create_process(db, name=body.name)
    if body.scope is not None:
        context_repository.set_process_scope(db, process.id, body.scope)
    return _to_summary(process, db)


@router.get("", response_model=list[ProcessSummary])
def list_processes(db: DbDep, user: CurrentUserDep) -> list[ProcessSummary]:
    del user
    return [_to_summary(p, db) for p in repository.list_processes(db)]


@router.get("/{process_id}", response_model=ProcessDetail)
def get_process(process_id: str, db: DbDep, user: CurrentUserDep) -> ProcessDetail:
    del user
    process = repository.get_process(db, process_id)
    schema = repository.get_process_schema(db, process_id)
    return ProcessDetail(**_to_summary(process, db).model_dump(), process_schema=schema)


@router.delete("/{process_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_process(process_id: str, db: DbDep, user: ProcessDeleteDep) -> None:
    del user
    repository.delete_process(db, process_id)
