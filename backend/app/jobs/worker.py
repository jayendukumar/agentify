"""Epic 31, US31.1: the job worker.

Runs inside the API process by default (started from app/main.py's
lifespan when `JOB_WORKER_ENABLED` is true), or on its own with
`python -m app.jobs.worker`; `JOB_WORKER_KINDS` limits which kinds a
worker claims (Epic 39: production executions on workers of their own). Several workers may run against one database:
claiming uses SKIP LOCKED, so a job runs once.

Handlers are async functions `(payload, session) -> dict | None`,
registered by kind. Each handler gets its own session and runs under a
per-kind concurrency limit (`JOB_CONCURRENCY`, default 2 per kind).

Epic 38, US38.3: jobs are tenant rows, invisible without a tenant. The
worker walks the active tenants (a platform table), claims each one's due
jobs as that tenant, and runs every job as its tenant. A suspended
tenant's jobs wait.

US38.9, fair share: free capacity is handed out one job per tenant per
round, to the tenants with queued work, those with the fewest jobs
running here first -- so a tenant with 10,000 queued jobs gets every
other free slot, not all of them, and a tenant with one job gets the next
slot that frees up. Within a tenant, jobs keep their priority and age
order. A tenant's own limit on concurrent jobs is counted in the database
(running jobs on every worker).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings, settings_or_exit
from app.db.models import JobModel
from app.db.session import get_session_factory
from app.tenancy.context import tenant_scope
from app.tenancy.hosts import active_tenant_ids
from app.tenancy.limits import TenantLimits, active_tenant_limits

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
    from app.runtime import jobs as _runtime_jobs  # noqa: F401
    from app.tenancy import export as _tenant_export  # noqa: F401


def _active_tenants() -> list[str]:
    with get_session_factory()() as session:
        return active_tenant_ids(session)


def _active_tenant_limits() -> list[TenantLimits]:
    with get_session_factory()() as session:
        return active_tenant_limits(session)


async def run_job(job_id: str, tenant_id: str) -> None:
    """Executes one claimed job, as its tenant, and records the outcome."""
    with tenant_scope(tenant_id):
        await _run_job(job_id)


async def _run_job(job_id: str) -> None:
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
    for tenant_id in _active_tenants():
        with tenant_scope(tenant_id):
            while ran < max_jobs:
                session = get_session_factory()()
                try:
                    claimed = queue.claim(session, "inline", kinds=kinds or list(_HANDLERS), limit=1)
                    session.commit()
                finally:
                    session.close()
                if not claimed:
                    break
                await run_job(claimed[0].id, tenant_id)
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
        self._by_tenant: dict[str, set[asyncio.Task]] = {}
        self._stopping = asyncio.Event()
        self._turn = 0
        # Epic 39, US39.3: `JOB_WORKER_KINDS=runtime_execution,runtime_submit,
        # runtime_callback,runtime_telemetry,runtime_alert` makes a worker
        # that runs production work and nothing else.
        kinds = settings.job_worker_kinds.strip()
        self.kinds = None if kinds in ("", "all") else {k.strip() for k in kinds.split(",") if k.strip()}

    def _capacity(self, kind: str) -> int:
        tasks = self._running.setdefault(kind, set())
        tasks -= {t for t in tasks if t.done()}
        return self.concurrency - len(tasks)

    def _running_here(self, tenant_id: str) -> int:
        tasks = self._by_tenant.setdefault(tenant_id, set())
        tasks -= {t for t in tasks if t.done()}
        return len(tasks)

    async def tick(self) -> None:
        _load_handlers()
        tenants = _active_tenant_limits()
        if not tenants:
            return
        self._turn = (self._turn + 1) % len(tenants)
        rotated = tenants[self._turn:] + tenants[: self._turn]
        ready = []
        for limits in rotated:
            # US38.14: one tenant's unreachable database holds up only that tenant.
            try:
                with tenant_scope(limits.tenant_id):
                    self._prepare_tenant()
                ready.append(limits)
            except Exception as exc:
                logger.warning("job_worker_tenant_skipped", extra={"tenant_id": limits.tenant_id, "error": str(exc)})
        rotated = ready
        # Least served here first; the rotation breaks ties.
        order = sorted(rotated, key=lambda t: self._running_here(t.tenant_id))
        for job_id, kind, tenant_id in self._claim_fairly(order):
            task = asyncio.create_task(run_job(job_id, tenant_id))
            self._running.setdefault(kind, set()).add(task)
            self._by_tenant.setdefault(tenant_id, set()).add(task)

    def _prepare_tenant(self) -> None:
        """Requeues the tenant's abandoned jobs, enqueues what's due and
        escalates overdue reviews."""
        from app.batches.service import enqueue_due_batches
        from app.connectors.jobs import enqueue_due_syncs
        from app.runtime.reviews import escalate_overdue

        session = get_session_factory()()
        try:
            queue.recover_expired(session, lease_seconds=self.lease_seconds)
            enqueue_due_syncs(session)
            enqueue_due_batches(session)
            escalate_overdue(session)  # Epic 39, US39.10: review SLAs
            session.commit()
        finally:
            session.close()

    def _claim_fairly(self, order: list[TenantLimits]) -> list[tuple[str, str, str]]:
        """Round-robin: each round, every tenant still holding due work
        claims one job of a kind with capacity left. A tenant drops out once
        it has nothing due, or is at its own limit."""
        free = {kind: self._capacity(kind) for kind in _HANDLERS if self.kinds is None or kind in self.kinds}
        claimed: list[tuple[str, str, str]] = []
        waiting = list(order)
        while waiting:
            still_waiting = []
            for limits in waiting:
                kinds = [kind for kind, n in free.items() if n > 0]
                if not kinds:
                    return claimed
                try:
                    with tenant_scope(limits.tenant_id):
                        job = self._claim_one(kinds, limits.max_concurrent_jobs)
                except Exception as exc:
                    logger.warning("job_worker_tenant_skipped", extra={"tenant_id": limits.tenant_id, "error": str(exc)})
                    continue
                if job is None:
                    continue
                free[job[1]] -= 1
                claimed.append((*job, limits.tenant_id))
                still_waiting.append(limits)
            waiting = still_waiting
        return claimed

    def _claim_one(self, kinds: list[str], max_concurrent: int | None) -> tuple[str, str] | None:
        session = get_session_factory()()
        try:
            if max_concurrent is not None:
                # One claimer per tenant at a time, across workers, so two
                # can't both take the last slot under the limit.
                session.execute(text("SELECT pg_advisory_xact_lock(hashtext('jobs:' || app_current_tenant()))"))
                running = session.scalar(select(func.count()).select_from(JobModel).where(JobModel.status == "running"))
                if running >= max_concurrent:
                    return None
            jobs = queue.claim(session, self.worker_id, kinds=kinds, limit=1)
            session.commit()
            return (jobs[0].id, jobs[0].kind) if jobs else None
        finally:
            session.close()

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
    from app.request_context import configure_logging

    configure_logging()
    asyncio.run(Worker().run())
