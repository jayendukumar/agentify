"""Epic 31, US31.10: an audit log of context access -- which person, LLM
call site, or connector read which context. Internal policies and system
topology are sensitive; this makes their use traceable."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ContextAccessLogModel
from app.ids import new_id


class AccessLogEntry(BaseModel):
    id: str
    at: datetime
    actor_type: str
    actor: str
    action: str
    process_id: str | None = None
    pack_id: str | None = None
    entity_ids: list[str] = []
    business_unit_id: str | None = None
    detail: str | None = None


def record(
    session: Session,
    *,
    actor_type: str,
    actor: str,
    action: str,
    process_id: str | None = None,
    pack_id: str | None = None,
    entity_ids: list[str] | None = None,
    business_unit_id: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        ContextAccessLogModel(
            id=new_id("ctxlog"),
            actor_type=actor_type,
            actor=actor,
            action=action,
            process_id=process_id,
            pack_id=pack_id,
            entity_ids=entity_ids or [],
            business_unit_id=business_unit_id,
            detail=detail,
        )
    )
    session.flush()


def list_entries(
    session: Session,
    *,
    limit: int = 200,
    actor_type: str | None = None,
    process_id: str | None = None,
    entity_id: str | None = None,
) -> list[AccessLogEntry]:
    query = select(ContextAccessLogModel).order_by(ContextAccessLogModel.at.desc())
    if actor_type:
        query = query.where(ContextAccessLogModel.actor_type == actor_type)
    if process_id:
        query = query.where(ContextAccessLogModel.process_id == process_id)
    rows = list(session.scalars(query.limit(limit * 5 if entity_id else limit)))
    if entity_id:
        rows = [r for r in rows if entity_id in (r.entity_ids or [])][:limit]
    return [
        AccessLogEntry(
            id=r.id,
            at=r.at,
            actor_type=r.actor_type,
            actor=r.actor,
            action=r.action,
            process_id=r.process_id,
            pack_id=r.pack_id,
            entity_ids=r.entity_ids or [],
            business_unit_id=r.business_unit_id,
            detail=r.detail,
        )
        for r in rows
    ]
