"""Epic 38, US38.13: an archive of everything a tenant holds, for its admin.

An admin asks for it (`POST /api/organization/exports`); a job, running
as the tenant, writes one zip file:

- `manifest.json`: the tenant, when, the schema revision, and how many
  rows each table and how many files went in;
- `tables/<table>.json`: every tenant table, one JSON array each, read in
  a single repeatable-read transaction so the tables agree with each
  other. The audit log is `tables/audit_events.json`;
- `files/...`: the tenant's uploaded files, as stored.

The archive is kept in the tenant's own directory (`_exports/`, not part
of the next export) and downloaded once: the download marks it used and
removes the file. One that isn't downloaded is removed after
TENANT_EXPORT_RETENTION_HOURS.

What isn't in it: sign-ins in flight and one-time links, and credential
hashes (app/tenancy/data.py). Secrets were never in the database: rows
hold references to them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import TenantExportModel, UserModel
from app.db.session import get_session_factory
from app.ids import new_id, utcnow
from app.jobs import queue
from app.jobs.worker import handler

from . import data
from .context import current_tenant_id
from .files import tenant_dir

logger = logging.getLogger(__name__)

EXPORTS_DIR = "_exports"
JOB_KIND = "tenant_export"
FORMAT_VERSION = 1


class ExportError(RuntimeError):
    pass


def _now() -> datetime:
    return utcnow().replace(tzinfo=None)


def archive_path(export_id: str) -> Path:
    return tenant_dir(get_settings()) / EXPORTS_DIR / f"{export_id}.zip"


def expire_old(db: Session) -> None:
    """Ready archives nobody downloaded in time are removed."""
    cutoff = _now() - timedelta(hours=get_settings().tenant_export_retention_hours)
    for export in db.scalars(select(TenantExportModel).where(TenantExportModel.status == "ready",
                                                              TenantExportModel.finished_at < cutoff)):
        archive_path(export.id).unlink(missing_ok=True)
        export.status = "expired"


def request_export(db: Session, user: UserModel) -> TenantExportModel:
    expire_old(db)
    busy = db.scalar(select(TenantExportModel).where(TenantExportModel.status.in_(("queued", "running"))))
    if busy is not None:
        raise ExportError("An export is already being made")
    export = TenantExportModel(id=new_id("texp"), status="queued", requested_by=user.id, requested_by_name=user.name,
                               created_at=_now(), table_counts={})
    db.add(export)
    db.flush()
    export.job_id = queue.enqueue(db, JOB_KIND, {"export_id": export.id}, max_attempts=1).id
    return export


def claim_download(db: Session, export_id: str, user: UserModel) -> Path:
    """Marks the archive used -- in one statement, so two downloads can't
    both get it -- and returns its file. The caller commits, then sends
    the file and removes it."""
    expire_old(db)
    claimed = db.execute(
        update(TenantExportModel)
        .where(TenantExportModel.id == export_id, TenantExportModel.status == "ready")
        .values(status="downloaded", downloaded_at=_now(), downloaded_by_name=user.name)
        .returning(TenantExportModel.id)
    ).first()
    if claimed is None:
        raise ExportError("This export isn't ready, or was already downloaded")
    path = archive_path(export_id)
    if not path.is_file():
        raise ExportError("The archive is missing; make a new export")
    return path


# ---- the job ------------------------------------------------------------------------


def schema_revision() -> str | None:
    """The migration the tables are at (the app role can't read
    alembic_version, so: the scripts' head, which a running app matches)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[2]
    try:
        return ScriptDirectory.from_config(Config(str(backend / "alembic.ini"))).get_current_head()
    except Exception:  # an installed app without its migrations
        return None


def _write_tables(archive: zipfile.ZipFile) -> dict[str, int]:
    counts: dict[str, int] = {}
    with get_session_factory()() as db:
        # Before the transaction begins: every table read from one snapshot.
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        for table in data.tenant_tables():
            if table.name in data.EXPORT_SKIPPED_TABLES:
                continue
            columns = data.exported_columns(table)
            n = 0
            with archive.open(f"tables/{table.name}.json", "w", force_zip64=True) as out:
                out.write(b"[")
                for row in db.execute(select(*columns).execution_options(yield_per=500)).mappings():
                    out.write((b",\n" if n else b"\n") + data.dumps(dict(row)))
                    n += 1
                out.write(b"\n]\n")
            counts[table.name] = n
        db.rollback()
    return counts


def _write_files(archive: zipfile.ZipFile) -> int:
    root = tenant_dir(get_settings())
    if not root.is_dir():
        return 0
    n = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] == EXPORTS_DIR or not path.is_file():
            continue
        archive.write(path, f"files/{relative.as_posix()}")
        n += 1
    return n


def build(export_id: str) -> dict[str, Any]:
    """Writes the archive, as the current tenant."""
    tenant_id = current_tenant_id()
    factory = get_session_factory()
    with factory() as db, db.begin():
        export = db.get(TenantExportModel, export_id)
        if export is None:
            raise ExportError(f"No export {export_id}")
        export.status = "running"
    path = archive_path(export_id)
    partial = path.with_suffix(".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            counts = _write_tables(archive)
            file_count = _write_files(archive)
            archive.writestr("manifest.json", json.dumps({
                "format": "axyntro-tenant-export", "format_version": FORMAT_VERSION, "tenant_id": tenant_id,
                "export_id": export_id, "created_at": _now().isoformat() + "Z", "schema_revision": schema_revision(),
                "tables": counts, "files": file_count, "audit_log": "tables/audit_events.json",
                "not_included": {
                    "tables": sorted(data.EXPORT_SKIPPED_TABLES),
                    "columns": sorted(f"{t}.{c}" for t, c in data.EXPORT_REDACTED_COLUMNS),
                    "secrets": "rows hold references (secret://tenant/<name>), never the secrets themselves",
                },
            }, indent=2))
        partial.replace(path)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        with factory() as db, db.begin():
            export = db.get(TenantExportModel, export_id)
            export.status, export.error, export.finished_at = "failed", f"{type(exc).__name__}: {exc}"[:2000], _now()
        raise
    with factory() as db, db.begin():
        export = db.get(TenantExportModel, export_id)
        export.status, export.finished_at = "ready", _now()
        export.size_bytes, export.file_count, export.table_counts = path.stat().st_size, file_count, counts
    logger.info("tenant_export_ready", extra={"export_id": export_id, "rows": sum(counts.values()), "files": file_count})
    return {"export_id": export_id, "rows": sum(counts.values()), "files": file_count}


@handler(JOB_KIND)
async def run_export(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    del session
    # Zipping is blocking work; the thread inherits the tenant context.
    return await asyncio.to_thread(build, payload["export_id"])
