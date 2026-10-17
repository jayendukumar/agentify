"""Epic 38, US38.13: an admin takes the organization's data with them --
every table as JSON, the files and the audit log, in one archive made by
a job (app/tenancy/export.py) and downloaded once."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.config import get_settings
from app.db.models import TenantExportModel
from app.schemas.data_export import TenantExportOut
from app.tenancy import export

from .deps import DbDep, OrgAdminDep

router = APIRouter(prefix="/api/organization/exports", tags=["organization"])


def _out(row: TenantExportModel) -> TenantExportOut:
    retention = timedelta(hours=get_settings().tenant_export_retention_hours)
    return TenantExportOut(
        id=row.id, status=row.status, requested_by_name=row.requested_by_name, created_at=row.created_at,
        finished_at=row.finished_at, downloaded_at=row.downloaded_at, downloaded_by_name=row.downloaded_by_name,
        size_bytes=row.size_bytes, file_count=row.file_count,
        rows=sum(row.table_counts.values()) if row.table_counts else None, error=row.error,
        expires_at=row.finished_at + retention if row.status == "ready" and row.finished_at else None,
    )


@router.get("", response_model=list[TenantExportOut])
def list_tenant_exports(db: DbDep, user: OrgAdminDep) -> list[TenantExportOut]:
    del user
    export.expire_old(db)
    rows = db.scalars(select(TenantExportModel).order_by(TenantExportModel.created_at.desc()).limit(20))
    return [_out(r) for r in rows]


@router.post("", response_model=TenantExportOut, status_code=202)
def tenant_export_requested(db: DbDep, user: OrgAdminDep) -> TenantExportOut:
    try:
        row = export.request_export(db, user)
    except export.ExportError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _out(row)


@router.get("/{export_id}/download")
def tenant_export_downloaded(export_id: str, db: DbDep, user: OrgAdminDep) -> FileResponse:
    """Once: the archive is marked used before it's sent, and removed after."""
    try:
        path = export.claim_download(db, export_id, user)
    except export.ExportError as exc:
        raise HTTPException(status.HTTP_410_GONE, detail=str(exc)) from exc
    db.commit()
    return FileResponse(path, media_type="application/zip", filename=f"axyntro-export-{export_id}.zip",
                        background=BackgroundTask(path.unlink, missing_ok=True))
