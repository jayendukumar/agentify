"""Epic 31, US31.1: the job worker.

Runs inside the API process by default (started from app/main.py's
lifespan when `JOB_WORKER_ENABLED` is true), or on its own with
`python -m app.jobs.worker`. Several workers may run against one database:
claiming uses SKIP LOCKED, so a job runs once.

Handlers are async functions `(payload, session) -> dict | None`,
registered by kind. Each handler gets its own session and runs under a
per-kind concurrency limit (`JOB_CONCURRENCY`, default 2 per kind).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings, settings_or_exit
from app.db.models import JobModel
from app.db.session import get_session_factory

from . import queue

logger = logging.getLogger("app.jobs")

Handler = Callable[[dict[str, Any], Session], Awaitable[dict[str, Any] | None]]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return register


def registered_kinds() -> list[str]:
    _load_handlers()
    return sorted(_HANDLERS)


def _load_handlers() -> None:
    # Imported for their @handler side effects; local to avoid import cycles.
    from app.connectors import jobs as _connector_jobs  # noqa: F401
    from app.context import jobs as _context_jobs  # noqa: F401
    from app.oracles import jobs as _oracle_jobs  # noqa: F401
    from app.process_twin import jobs as _process_twin_jobs  # noqa: F401


async def run_job(job_id: str) -> None:
    """Executes one claimed job in its own session and records the outcome."""
    _load_handlers()
    session = get_session_factory()()
    try:
        job = session.get(JobModel, job_id)
        if job is None:
            return
        fn = _HANDLERS.get(job.kind)
        if fn is None:
            queue.fail(session, job, f"No handler for job kind '{job.kind}'")
            job.status = "failed"
            session.commit()
            return
        try:
            result = await fn(dict(job.payload or {}), session)
            session.commit()
            job = session.get(JobModel, job_id)
            queue.complete(session, job, result)
            session.commit()
        except Exception as exc:  # the job's own failure -- recorded, retried
            session.rollback()
            logger.warning("job_failed", extra={"job_id": job_id, "kind": job.kind, "error": str(exc)})
            job = session.get(JobModel, job_id)
            queue.fail(session, job, f"{type(exc).__name__}: {exc}", backoff_base_seconds=get_settings().job_backoff_seconds)
            session.commit()
    finally:
        session.close()


async def run_pending(*, kinds: list[str] | None = None, max_jobs: int = 100) -> int:
    """Runs every due job once, sequentially -- for tests and one-off use.
    Returns how many ran."""
    _load_handlers()
    ran = 0
    while ran < max_jobs:
        session = get_session_factory()()
        try:
            claimed = queue.claim(session, "inline", kinds=kinds or list(_HANDLERS), limit=1)
            session.commit()
        finally:
            session.close()
        if not claimed:
            return ran
        await run_job(claimed[0].id)
        ran += 1
    return ran


class Worker:
    def __init__(self) -> None:
        settings = get_settings()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.poll_seconds = settings.job_poll_seconds
        self.concurrency = settings.job_concurrency
        self.lease_seconds = settings.job_lease_seconds
        self._running: dict[str, set[asyncio.Task]] = {}
        self._stopping = asyncio.Event()

    def _capacity(self, kind: str) -> int:
        tasks = self._running.setdefault(kind, set())
        tasks -= {t for t in tasks if t.done()}
        return self.concurrency - len(tasks)

    async def tick(self) -> None:
        _load_handlers()
        session = get_session_factory()()
        try:
            queue.recover_expired(session, lease_seconds=self.lease_seconds)
            from app.batches.service import enqueue_due_batches
            from app.connectors.jobs import enqueue_due_syncs

            enqueue_due_syncs(session)
            enqueue_due_batches(session)
            claimed: list[JobModel] = []
            for kind in _HANDLERS:
                claimed += queue.claim(session, self.worker_id, kinds=[kind], limit=self._capacity(kind))
            session.commit()
            job_ids = [(job.id, job.kind) for job in claimed]
        finally:
            session.close()
        for job_id, kind in job_ids:
            task = asyncio.create_task(run_job(job_id))
            self._running.setdefault(kind, set()).add(task)

    async def run(self) -> None:
        logger.info("job_worker_started", extra={"worker_id": self.worker_id})
        while not self._stopping.is_set():
            try:
                await self.tick()
            except Exception as exc:  # keep the loop alive; the next tick retries
                logger.warning("job_worker_tick_failed", extra={"error": str(exc)})
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopping.set()


if __name__ == "__main__":
    # Epic 37, US37.14: settings validation refuses unsafe auth config
    # (e.g. local sign-in in production). Check before polling the queue so
    # a standalone worker exits non-zero instead of running half-configured.
    settings_or_exit("job worker")
    asyncio.run(Worker().run())
