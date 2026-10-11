"""Epic 32: labelled datasets and ground truth API."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import TypeAdapter, ValidationError

from app.datasets import service
from app.schemas.datasets import (
    AmbiguityUpdate,
    ColumnMapping,
    DatasetCreate,
    DatasetDiff,
    DatasetRow,
    DatasetRowInput,
    DatasetRowPage,
    DatasetRowUpdate,
    DatasetSummary,
    HoldoutRequest,
    ImportPreview,
    ImportResult,
    PromoteRunRequest,
)

from .deps import CurrentUserDep, DbDep, EditorDep

router = APIRouter(prefix="/api/processes/{process_id}/datasets", tags=["datasets"])

_MAX_IMPORT_BYTES = 20 * 1024 * 1024


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("", response_model=DatasetSummary, status_code=status.HTTP_201_CREATED)
def create_dataset(process_id: str, body: DatasetCreate, db: DbDep, user: EditorDep) -> DatasetSummary:
    return service.summarize(db, service.create_dataset(db, process_id, body, created_by=user.id))


@router.get("", response_model=list[DatasetSummary])
def list_datasets(process_id: str, db: DbDep, user: CurrentUserDep) -> list[DatasetSummary]:
    del user
    return service.list_datasets(db, process_id)


@router.get("/{dataset_id}", response_model=DatasetSummary)
def get_dataset(process_id: str, dataset_id: str, db: DbDep, user: CurrentUserDep) -> DatasetSummary:
    del user
    return service.summarize(db, service.get_dataset_model(db, process_id, dataset_id))


@router.get("/{dataset_id}/rows", response_model=DatasetRowPage)
def list_rows(
    process_id: str,
    dataset_id: str,
    db: DbDep,
    user: CurrentUserDep,
    version: int | None = None,
    segment: str | None = None,
    provenance: str | None = None,
    only: str | None = Query(default=None, pattern="^(unconfirmed|ambiguous|holdout|critical)$"),
    include_holdout: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
) -> DatasetRowPage:
    """US32.8: holdout rows' labels are hidden unless `include_holdout` --
    so they aren't looked at while the agent is being tuned."""
    del user
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    return service.list_rows(
        db, dataset, version=version, segment=segment, provenance=provenance, only=only,
        include_holdout=include_holdout, offset=offset, limit=limit,
    )


@router.post("/{dataset_id}/rows", response_model=DatasetSummary)
def add_rows(process_id: str, dataset_id: str, body: list[DatasetRowInput], db: DbDep, user: EditorDep) -> DatasetSummary:
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    try:
        service.add_rows(db, dataset, body, created_by=user.id)
    except service.DatasetError as exc:
        raise _bad_request(exc) from exc
    return service.summarize(db, dataset)


@router.patch("/{dataset_id}/rows/{row_key}", response_model=DatasetRow)
def update_row(
    process_id: str, dataset_id: str, row_key: str, body: DatasetRowUpdate, db: DbDep, user: EditorDep
) -> DatasetRow:
    """US32.4: correct or confirm a label (recorded with who did it)."""
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    return service.to_row(service.update_row(db, dataset, row_key, body, changed_by=user.id), include_holdout=True)


@router.put("/{dataset_id}/rows/{row_key}/ambiguity", response_model=DatasetRow)
def set_ambiguity(
    process_id: str, dataset_id: str, row_key: str, body: AmbiguityUpdate, db: DbDep, user: EditorDep
) -> DatasetRow:
    """US32.5."""
    del user
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    return service.to_row(service.set_ambiguity(db, dataset, row_key, body), include_holdout=True)


@router.delete("/{dataset_id}/rows/{row_key}", response_model=DatasetSummary)
def remove_row(process_id: str, dataset_id: str, row_key: str, db: DbDep, user: EditorDep) -> DatasetSummary:
    del user
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    service.remove_row(db, dataset, row_key)
    return service.summarize(db, dataset)


@router.get("/{dataset_id}/diff", response_model=DatasetDiff)
def diff(
    process_id: str, dataset_id: str, db: DbDep, user: CurrentUserDep, from_version: int = Query(alias="from"), to_version: int | None = Query(default=None, alias="to")
) -> DatasetDiff:
    """US32.6."""
    del user
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    return service.diff(db, dataset, from_version, to_version or dataset.version)


@router.post("/{dataset_id}/holdout", response_model=DatasetSummary)
def split_holdout(process_id: str, dataset_id: str, body: HoldoutRequest, db: DbDep, user: EditorDep) -> DatasetSummary:
    """US32.8."""
    del user
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    service.split_holdout(db, dataset, fraction=body.fraction, seed=body.seed)
    return service.summarize(db, dataset)


@router.post("/{dataset_id}/import/preview", response_model=ImportPreview)
async def preview_import(process_id: str, dataset_id: str, db: DbDep, user: EditorDep, file: UploadFile = File(...)) -> ImportPreview:
    """US32.3, step 1: read the file and suggest a column mapping. Nothing
    is stored."""
    del user
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    content = await file.read()
    if len(content) > _MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File exceeds the 20MB limit")
    try:
        return service.preview_import(db, dataset, content, file.filename or "data.csv")
    except service.DatasetError as exc:
        raise _bad_request(exc) from exc


@router.post("/{dataset_id}/import", response_model=ImportResult)
async def import_rows(
    process_id: str,
    dataset_id: str,
    db: DbDep,
    user: EditorDep,
    file: UploadFile = File(...),
    mapping: str = Form(...),
) -> ImportResult:
    """US32.3, step 2: import with the confirmed mapping. Columns marked
    hash/fake are pseudonymized locally before anything is stored."""
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    content = await file.read()
    if len(content) > _MAX_IMPORT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File exceeds the 20MB limit")
    try:
        columns = TypeAdapter(list[ColumnMapping]).validate_python(json.loads(mapping))
        return service.import_rows(db, dataset, content, file.filename or "data.csv", columns, created_by=user.id)
    except (json.JSONDecodeError, ValidationError, service.DatasetError) as exc:
        raise _bad_request(exc) from exc


@router.post("/{dataset_id}/rows/from-run", response_model=DatasetRow)
def promote_run(process_id: str, dataset_id: str, body: PromoteRunRequest, db: DbDep, user: EditorDep) -> DatasetRow:
    """US32.7: a failed or surprising run becomes a golden case."""
    dataset = service.get_dataset_model(db, process_id, dataset_id)
    try:
        return service.to_row(service.promote_run(db, dataset, body, created_by=user.id), include_holdout=True)
    except service.DatasetError as exc:
        raise _bad_request(exc) from exc
