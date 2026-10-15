"""Epic 37, US37.6: the append-only audit trail.

`record` writes in the caller's transaction, so an event exists exactly
when the action it describes was committed. `record_refusal` is the
exception: a refused request rolls back, so refusals are written in their
own short transaction or they would vanish with it.

Epic 31's `context_access_log` (app/context/audit.py) stays separate: it is
about who *read* which context, at much higher volume.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditEventModel, UserModel
from app.ids import new_id

logger = logging.getLogger(__name__)


def _client(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def _event(
    *,
    actor: UserModel | None,
    action: str,
    object_type: str | None,
    object_id: str | None,
    process_id: str | None,
    outcome: str,
    request: Request | None,
    detail: dict[str, Any] | None,
    actor_name: str | None = None,
) -> AuditEventModel:
    ip, user_agent = _client(request)
    return AuditEventModel(
        id=new_id("audit"),
        actor_type=actor.actor_type if actor is not None else "system",
        actor_id=actor.id if actor is not None else None,
        actor_name=actor.name if actor is not None else actor_name,
        action=action,
        object_type=object_type,
        object_id=object_id,
        process_id=process_id,
        outcome=outcome,
        ip=ip,
        user_agent=user_agent,
        detail=detail or {},
    )


def record(
    session: Session,
    *,
    actor: UserModel | None,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    process_id: str | None = None,
    request: Request | None = None,
    detail: dict[str, Any] | None = None,
    actor_name: str | None = None,
    outcome: str = "success",
) -> None:
    """`outcome="refused"` is for a refusal the caller commits on purpose
    (a reused refresh token revokes the session); `record_refusal` covers
    the usual case, where the request rolls back."""
    session.add(
        _event(
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=object_id,
            process_id=process_id,
            outcome=outcome,
            request=request,
            detail=detail,
            actor_name=actor_name,
        )
    )


def record_refusal(
    *,
    actor: UserModel | None,
    action: str,
    reason: str,
    object_type: str | None = None,
    object_id: str | None = None,
    process_id: str | None = None,
    request: Request | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    from app.db.session import get_session_factory

    event = _event(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        process_id=process_id,
        outcome="refused",
        request=request,
        detail={**(detail or {}), "reason": reason},
    )
    try:
        with get_session_factory()() as session, session.begin():
            session.add(event)
    except Exception:  # never let the audit write mask the refusal itself
        logger.exception("Failed to record refused %s by %s", action, event.actor_id)


def target_of(path_params: dict[str, str]) -> tuple[str | None, str | None]:
    """The most specific object a route's path names."""
    if "artifact_id" in path_params:
        return "agent", path_params["artifact_id"]
    if "version_id" in path_params:
        return "process_version", path_params["version_id"]
    if "process_id" in path_params:
        return "process", path_params["process_id"]
    for key, value in path_params.items():
        if key.endswith("_id"):
            return key[:-3], value
    return None, None
