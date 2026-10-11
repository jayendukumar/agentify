"""Epic 31, US31.2-US31.6: connector management -- configure read-only
connectors to systems of record, sync them now or on a schedule, and see
each sync's results and the impact of what changed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.connectors.sync import CONNECTOR_TYPES, connector_summary, run_sync
from app.context import access as context_access
from app.context.audit import record
from app.db.models import ConnectorModel, ConnectorRunModel, JobModel
from app.ids import new_id
from app.jobs import queue

from .deps import CurrentUserDep, DbDep, EditorDep

router = APIRouter(prefix="/api/organization/connectors", tags=["connectors"])

# Secrets never go into stored config -- name an environment variable
# with `token_env` instead.
_SECRET_WORDS = ("token", "password", "secret", "api_key", "apikey", "client_secret")


class ConnectorConfig(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: str
    config: dict[str, Any] = {}
    enabled: bool = True
    schedule_minutes: int | None = Field(default=None, ge=5)
    owner_business_unit_id: str | None = None


class Connector(ConnectorConfig):
    id: str
    last_sync_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None


class ConnectorRun(BaseModel):
    id: str
    status: str
    stats: dict[str, int] = {}
    impacts: list[dict[str, Any]] = []
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class SyncStarted(BaseModel):
    queued: bool
    job_id: str | None = None
    run: ConnectorRun | None = None


def _validate(body: ConnectorConfig) -> None:
    if body.type not in CONNECTOR_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown connector type '{body.type}'")
    for key in body.config:
        if key != "token_env" and any(word in key.lower() for word in _SECRET_WORDS):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Don't store '{key}' in connector config -- put it in an environment variable and set token_env",
            )


def _to_run(run: ConnectorRunModel) -> ConnectorRun:
    return ConnectorRun(
        id=run.id, status=run.status, stats=run.stats or {}, impacts=run.impacts or [], error=run.error,
        started_at=run.started_at, finished_at=run.finished_at,
    )


def _get(db, connector_id: str) -> ConnectorModel:
    connector = db.get(ConnectorModel, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"connector '{connector_id}' not found")
    return connector


@router.get("/types")
def connector_types(user: CurrentUserDep) -> list[dict[str, str]]:
    del user
    return [{"type": t, "description": c.description} for t, c in sorted(CONNECTOR_TYPES.items())]


@router.get("", response_model=list[Connector])
def list_connectors(db: DbDep, user: CurrentUserDep) -> list[Connector]:
    access = context_access.access_for(db, user)
    return [
        Connector.model_validate(connector_summary(c))
        for c in db.scalars(select(ConnectorModel).order_by(ConnectorModel.name))
        if access.unrestricted or c.owner_business_unit_id in access.units
    ]


@router.post("", response_model=Connector, status_code=status.HTTP_201_CREATED)
def create_connector(body: ConnectorConfig, db: DbDep, user: EditorDep) -> Connector:
    _validate(body)
    owner = context_access.owner_for_new(context_access.access_for(db, user), body.owner_business_unit_id)
    if db.scalar(select(ConnectorModel).where(ConnectorModel.name == body.name)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"A connector named '{body.name}' already exists")
    connector = ConnectorModel(
        id=new_id("conn"),
        name=body.name,
        type=body.type,
        config=body.config,
        enabled=body.enabled,
        schedule_minutes=body.schedule_minutes,
        owner_business_unit_id=owner,
        created_by=user.id,
    )
    db.add(connector)
    db.flush()
    return Connector.model_validate(connector_summary(connector))


@router.put("/{connector_id}", response_model=Connector)
def update_connector(connector_id: str, body: ConnectorConfig, db: DbDep, user: EditorDep) -> Connector:
    _validate(body)
    connector = _get(db, connector_id)
    access = context_access.access_for(db, user)
    if not access.unrestricted and connector.owner_business_unit_id not in access.steward_units:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a steward of the owning unit can change this connector")
    connector.name = body.name
    connector.type = body.type
    connector.config = body.config
    connector.enabled = body.enabled
    connector.schedule_minutes = body.schedule_minutes
    db.flush()
    return Connector.model_validate(connector_summary(connector))


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connector(connector_id: str, db: DbDep, user: EditorDep) -> None:
    """Deleting a connector keeps what it synced (as ordinary context with
    its connector source) -- removing context is a separate decision."""
    connector = _get(db, connector_id)
    access = context_access.access_for(db, user)
    if not access.unrestricted and connector.owner_business_unit_id not in access.steward_units:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a steward of the owning unit can delete this connector")
    db.delete(connector)
    db.flush()


@router.post("/{connector_id}/sync", response_model=SyncStarted)
async def sync_now(connector_id: str, db: DbDep, user: EditorDep) -> SyncStarted:
    """Queued when the job worker runs; otherwise synced in this request."""
    connector = _get(db, connector_id)
    record(db, actor_type="connector", actor=connector.name, action="sync_requested", detail=f"by {user.id}")
    if get_settings().job_worker_enabled:
        job = queue.enqueue(db, "connector_sync", {"connector_id": connector.id}, dedupe_key=f"sync:{connector.id}")
        db.commit()
        return SyncStarted(queued=True, job_id=job.id)
    run = await run_sync(db, connector)
    if run.status == "succeeded":
        # Ingest any policy documents the sync found, in this request too.
        from app.jobs.worker import run_pending

        db.commit()
        await run_pending(kinds=["policy_ingest"])
    return SyncStarted(queued=False, run=_to_run(run))


@router.get("/{connector_id}/runs", response_model=list[ConnectorRun])
def list_runs(connector_id: str, db: DbDep, user: CurrentUserDep) -> list[ConnectorRun]:
    del user
    _get(db, connector_id)
    rows = db.scalars(
        select(ConnectorRunModel)
        .where(ConnectorRunModel.connector_id == connector_id)
        .order_by(ConnectorRunModel.started_at.desc())
        .limit(50)
    )
    return [_to_run(r) for r in rows]


class JobView(BaseModel):
    id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@jobs_router.get("", response_model=list[JobView])
def list_jobs(db: DbDep, user: CurrentUserDep, status_filter: str | None = None, limit: int = 100) -> list[JobView]:
    """US31.1: what the queue is doing."""
    del user
    query = select(JobModel).order_by(JobModel.created_at.desc()).limit(min(limit, 500))
    if status_filter:
        query = query.where(JobModel.status == status_filter)
    return [
        JobView(
            id=j.id, kind=j.kind, status=j.status, attempts=j.attempts, max_attempts=j.max_attempts,
            last_error=j.last_error, created_at=j.created_at, finished_at=j.finished_at,
        )
        for j in db.scalars(query)
    ]
