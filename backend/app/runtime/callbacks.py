"""Epic 39, US39.4: telling the caller's system that a case finished.

When an execution started with a `callback_url` ends, a `runtime_callback`
job POSTs its result there. The job retries with backoff until the caller
answers 2xx (5 attempts), so a caller that is briefly down still hears.

The body is signed with the runtime client's signing secret (shown once
when the client is created):

    Axyntro-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">

The caller recomputes the HMAC over the raw body and rejects a stale `t`,
so a forged or replayed callback is refused. Only hosts in
RUNTIME_CALLBACK_HOSTS are called, checked again at send time, and
redirects aren't followed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AgentExecutionModel, RuntimeClientModel
from app.jobs import queue
from app.jobs.worker import handler

JOB_KIND = "runtime_callback"
SIGNATURE_HEADER = "Axyntro-Signature"


def allowed(url: str) -> bool:
    parsed = urlparse(url)
    patterns = [p.strip() for p in get_settings().runtime_callback_hosts.split(",") if p.strip()]
    host = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and any(fnmatch.fnmatch(host, p) for p in patterns)


def sign(secret: str, body: bytes, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def notify_caller(session: Session, execution: AgentExecutionModel) -> None:
    """Queues the callback of an execution that just ended, if it asked for one."""
    if execution.callback_url:
        queue.enqueue(session, JOB_KIND, {"execution_id": execution.id}, max_attempts=5,
                      dedupe_key=f"runtime-callback:{execution.id}")


def _utc(at: datetime | None) -> str | None:
    """Columns hold naive UTC; the caller gets it marked as UTC."""
    return at.replace(tzinfo=at.tzinfo or timezone.utc).isoformat() if at else None


def _body(execution: AgentExecutionModel) -> bytes:
    return json.dumps({
        "execution_id": execution.id,
        "deployment_id": execution.deployment_id,
        "status": execution.status,
        "final_output": execution.final_output,
        "completed_at": _utc(execution.completed_at),
    }, separators=(",", ":")).encode()


@handler(JOB_KIND)
async def runtime_callback(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    execution = session.get(AgentExecutionModel, payload["execution_id"])
    if execution is None or not execution.callback_url:
        return {"skipped": True}
    if not allowed(execution.callback_url):
        return {"skipped": True, "reason": "the callback host is no longer allowed"}
    client = session.get(RuntimeClientModel, execution.client_id) if execution.client_id else None
    body = _body(execution)
    headers = {"Content-Type": "application/json"}
    if client is not None and client.signing_secret:
        headers[SIGNATURE_HEADER] = sign(client.signing_secret, body, int(time.time()))
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as http:
        response = await http.post(execution.callback_url, content=body, headers=headers)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"the callback answered {response.status_code}")  # retried with backoff
    return {"status_code": response.status_code}
