"""Epic 31, US31.2-US31.6 and US31.9: apply what a connector read.

Entities synced from a system of record are authoritative there, so they
are created and updated as confirmed -- unlike LLM extraction (Epic 27),
which always proposes. A change bumps the entity's version, which is what
makes Epic 28's pinned packs go stale and what impact analysis reports.
Policy *documents* are different: they still go through Epic 27
extraction, whose proposals a person confirms.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.context import ingestion
from app.context import repository as context_repository
from app.context.consumers import compute_impact
from app.db.models import ConnectorModel, ConnectorRunModel, ContextDocumentModel, ContextEntityModel
from app.ids import new_id, utcnow
from app.jobs import queue
from app.schemas.context import ContextEntityCreate, ContextEntityUpdate

from .base import Connector, ConnectorError, SyncBatch, SyncItem
from .mcp import McpConnector
from .sources import FolderConnector, HttpConnector, OscalConnector, ScimConnector

CONNECTOR_TYPES: dict[str, type[Connector]] = {
    c.type: c for c in (McpConnector, FolderConnector, HttpConnector, OscalConnector, ScimConnector)
}


def build_connector(connector: ConnectorModel) -> Connector:
    cls = CONNECTOR_TYPES.get(connector.type)
    if cls is None:
        raise ConnectorError(f"Unknown connector type '{connector.type}'")
    return cls(connector.config or {})


def _synced(session: Session, source: str, kind: str | None = None) -> dict[str, ContextEntityModel]:
    query = select(ContextEntityModel).where(ContextEntityModel.source == source, ContextEntityModel.status == "confirmed")
    if kind is not None:
        query = query.where(ContextEntityModel.kind == kind)
    return {e.external_id: e for e in session.scalars(query) if e.external_id}


def _apply_entity(
    session: Session, item: SyncItem, existing: ContextEntityModel | None, *, source: str, owner: str | None
) -> str:
    if existing is None:
        entity = context_repository.create_entity(
            session,
            ContextEntityCreate(
                kind=item.kind,  # type: ignore[arg-type]
                name=item.name,
                code=item.code,
                data=item.data,
                applies_to=item.applies_to,
                owner_business_unit_id=owner,
            ),
            source=source,
        )
        entity.external_id = item.external_id
        session.flush()
        return "created"
    before = existing.version
    merged = {**(existing.data or {}), **item.data}
    context_repository.update_entity(
        session,
        existing.id,
        ContextEntityUpdate(
            name=item.name, code=item.code, data=merged, applies_to=item.applies_to, change_note=f"synced from {source}"
        ),
    )
    return "updated" if existing.version != before else "unchanged"


def _apply_document(session: Session, item: SyncItem, *, source: str, owner: str | None) -> tuple[str, str | None]:
    """Returns (outcome, document id to ingest)."""
    assert item.content is not None
    digest = ingestion.content_hash(item.content)
    existing = _synced(session, source, "policy").get(item.external_id)
    if existing is not None:
        current_doc_id = (existing.data or {}).get("document_id")
        current = session.get(ContextDocumentModel, current_doc_id) if current_doc_id else None
        if current is not None and current.content_hash == digest:
            return "unchanged", None
    document_id = new_id("ctxdoc")
    data = {"summary": "", "document_id": document_id, "effective_date": None}
    if existing is None:
        policy = context_repository.create_entity(
            session,
            ContextEntityCreate(kind="policy", name=item.name, data=data, owner_business_unit_id=owner),
            source=source,
        )
        policy.external_id = item.external_id
        outcome = "created"
    else:
        policy = context_repository.update_entity(
            session, existing.id, ContextEntityUpdate(data=data, change_note=f"new version synced from {source}")
        )
        outcome = "updated"
    ingestion.save_policy_file(get_settings(), document_id, item.filename or item.name, item.content)
    session.add(
        ContextDocumentModel(
            id=document_id,
            filename=item.filename or item.name,
            content_type=item.content_type or "application/octet-stream",
            size_bytes=len(item.content),
            status="pending",
            policy_entity_id=policy.id,
            content_hash=digest,
        )
    )
    session.flush()
    return outcome, document_id


async def run_sync(session: Session, connector: ConnectorModel) -> ConnectorRunModel:
    run = ConnectorRunModel(id=new_id("connrun"), connector_id=connector.id, status="running", stats={})
    session.add(run)
    session.flush()
    source = f"connector:{connector.name}"
    stats: dict[str, int] = {"created": 0, "updated": 0, "unchanged": 0, "retired": 0, "documents_queued": 0}
    changed: list[str] = []
    try:
        batch: SyncBatch = await build_connector(connector).fetch()
        seen: dict[str, set[str]] = {}
        for item in batch.items:
            seen.setdefault(item.kind, set()).add(item.external_id)
            if item.kind == "policy_document":
                outcome, document_id = _apply_document(session, item, source=source, owner=connector.owner_business_unit_id)
                stats[outcome] += 1
                if document_id is not None:
                    queue.enqueue(session, "policy_ingest", {"document_id": document_id}, dedupe_key=f"ingest:{document_id}")
                    stats["documents_queued"] += 1
                    existing_policy = _synced(session, source, "policy").get(item.external_id)
                    if existing_policy is not None and outcome == "updated":
                        changed.append(existing_policy.id)
                continue
            existing = _synced(session, source, item.kind).get(item.external_id)
            outcome = _apply_entity(session, item, existing, source=source, owner=connector.owner_business_unit_id)
            stats[outcome] += 1
            if outcome == "updated" and existing is not None:
                changed.append(existing.id)
        if batch.complete:
            for kind, ids in seen.items():
                entity_kind = "policy" if kind == "policy_document" else kind
                for external_id, entity in _synced(session, source, entity_kind).items():
                    if external_id not in ids:
                        context_repository.retire_entity(session, entity.id)
                        stats["retired"] += 1
                        changed.append(entity.id)
        # US31.9: what each change affects, recorded with the run.
        run.impacts = [compute_impact(session, entity_id).model_dump() for entity_id in changed]
        run.status = "succeeded"
        connector.last_status = "succeeded"
        connector.last_error = None
    except (ConnectorError, ValueError) as exc:
        run.status = "failed"
        run.error = str(exc)
        connector.last_status = "failed"
        connector.last_error = str(exc)
    run.stats = stats
    run.finished_at = utcnow().replace(tzinfo=None)
    connector.last_sync_at = run.finished_at
    session.flush()
    return run


def connector_summary(connector: ConnectorModel) -> dict[str, Any]:
    return {
        "id": connector.id,
        "name": connector.name,
        "type": connector.type,
        "config": connector.config,
        "enabled": connector.enabled,
        "schedule_minutes": connector.schedule_minutes,
        "owner_business_unit_id": connector.owner_business_unit_id,
        "last_sync_at": connector.last_sync_at,
        "last_status": connector.last_status,
        "last_error": connector.last_error,
    }
