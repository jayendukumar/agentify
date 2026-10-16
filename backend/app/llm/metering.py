"""Epic 38, US38.9: each tenant's LLM usage, and the limits on it.

Every LLM call made inside a tenant (a request, or a job run as its
tenant) goes through here twice:

- **before** (`admit`): refused once the month's budget is spent, and held
  back while the tenant has made its requests-per-minute in the last 60
  seconds. Crossing 80% of the budget is written to the tenant's audit
  log, once a month; reaching it, too.
- **after** (`record`): one `llm_usage_events` row, and the month's total
  in `llm_usage_monthly`, both as the tenant.

Calls made outside any tenant (scripts) are neither limited nor recorded
here; the JSONL log (app/llm/usage.py) still has them.

Requests per minute are counted in this process: with a separate worker
process, a tenant can make that many in each. The budget and concurrent
jobs are counted in the database, across processes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import AuditEventModel, LlmUsageEventModel, LlmUsageMonthlyModel
from app.ids import new_id
from app.tenancy.context import current_tenant_id, tenant_scope
from app.tenancy.limits import BUDGET_WARN_RATIO, limits_for

from .exceptions import LLMBudgetError
from .types import Usage
from .usage import estimate_cost_usd

logger = logging.getLogger("app.llm.metering")

BudgetStatus = Literal["unlimited", "ok", "warning", "stopped"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def month_of(moment: datetime) -> str:
    return moment.strftime("%Y-%m")


def month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_spend(session: Session, month: str | None = None, *, key_source: str | None = "platform") -> float:
    """What this tenant's LLM calls cost this month (UTC), as far as prices
    are known. US38.11: on the platform's key by default -- what the budget
    limits; `key_source=None` for every call, whoever paid."""
    month = month or month_of(utcnow())
    query = select(func.sum(LlmUsageMonthlyModel.cost_usd)).where(LlmUsageMonthlyModel.month == month)
    if key_source is not None:
        query = query.where(LlmUsageMonthlyModel.key_source == key_source)
    return float(session.scalar(query) or 0.0)


@dataclass(frozen=True)
class BudgetState:
    spent_usd: float
    budget_usd: float | None
    status: BudgetStatus


def budget_state(session: Session, budget_usd: float | None) -> BudgetState:
    spent = month_spend(session)
    if budget_usd is None:
        return BudgetState(spent, None, "unlimited")
    if spent >= budget_usd:
        return BudgetState(spent, budget_usd, "stopped")
    if spent >= BUDGET_WARN_RATIO * budget_usd:
        return BudgetState(spent, budget_usd, "warning")
    return BudgetState(spent, budget_usd, "ok")


def budget_reached(session: Session) -> bool:
    """For the batch gates: stop a batch, rather than fail its runs one by
    one, once the tenant's month is spent."""
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return False
    from .keys import tenant_key_ref

    if tenant_key_ref():  # US38.11: on its own key, the platform's quota doesn't apply
        return False
    return budget_state(session, limits_for(tenant_id).llm_monthly_budget_usd).status == "stopped"


# ---- audit, once a month ----------------------------------------------------------

_announced: set[tuple[str, str, str]] = set()


def _announce_once(session: Session, tenant_id: str, action: str, state: BudgetState) -> None:
    now = utcnow()
    key = (tenant_id, month_of(now), action)
    if key in _announced:
        return
    already = session.scalar(
        select(AuditEventModel.id).where(AuditEventModel.action == action, AuditEventModel.at >= month_start(now)).limit(1)
    )
    if already is None:
        session.add(AuditEventModel(
            id=new_id("audit"), actor_type="system", actor_name="LLM budget", action=action,
            detail={"month": month_of(now), "spent_usd": round(state.spent_usd, 4), "budget_usd": state.budget_usd},
        ))
        session.commit()
        logger.warning(action, extra={"tenant_id": tenant_id, "spent_usd": state.spent_usd,
                                      "budget_usd": state.budget_usd})
    _announced.add(key)


def _check_budget(tenant_id: str) -> None:
    from app.db.session import get_session_factory

    budget = limits_for(tenant_id).llm_monthly_budget_usd
    if budget is None:
        return
    with tenant_scope(tenant_id), get_session_factory()() as session:
        state = budget_state(session, budget)
        if state.status == "stopped":
            _announce_once(session, tenant_id, "llm_budget_reached", state)
            raise LLMBudgetError(
                f"This month's LLM budget of ${budget:.2f} is used up (${state.spent_usd:.2f} spent). "
                "An operator can raise it."
            )
        if state.status == "warning":
            _announce_once(session, tenant_id, "llm_budget_warning", state)


# ---- requests per minute ------------------------------------------------------------

_WINDOW_SECONDS = 60.0
_windows: dict[str, deque[float]] = {}


async def _within_rate(tenant_id: str, per_minute: int) -> None:
    """Waits until the tenant has made fewer than `per_minute` calls in the
    last minute (from this process), then counts this one. No lock: the
    check and the append run without an `await` between them."""
    calls = _windows.setdefault(tenant_id, deque())
    while True:
        now = time.monotonic()
        while calls and now - calls[0] >= _WINDOW_SECONDS:
            calls.popleft()
        if len(calls) < per_minute:
            calls.append(now)
            return
        await asyncio.sleep(_WINDOW_SECONDS - (now - calls[0]) + 0.01)


async def admit(key_source: str = "platform") -> None:
    """Before an LLM call: refuses it over budget, and waits out the rate.
    US38.11: the budget is the platform's quota, so a call on the tenant's
    own key isn't held to it; the rate still applies."""
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return
    if key_source == "platform":
        await asyncio.to_thread(_check_budget, tenant_id)
    per_minute = limits_for(tenant_id).llm_requests_per_minute
    if per_minute is not None:
        await _within_rate(tenant_id, per_minute)


# ---- recording ----------------------------------------------------------------------


def _record(tenant_id: str, operation: str, model: str, usage: Usage, key_source: str) -> None:
    from app.db.session import get_session_factory

    cost = estimate_cost_usd(model, usage)
    now = utcnow()
    with tenant_scope(tenant_id), get_session_factory()() as session:
        session.add(LlmUsageEventModel(
            occurred_at=now, operation=operation, model=model, input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens, total_tokens=usage.total_tokens, estimated_cost_usd=cost,
            key_source=key_source,
        ))
        row = {
            "month": month_of(now), "operation": operation, "model": model, "key_source": key_source, "calls": 1,
            "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens, "cost_usd": cost or 0.0, "calls_without_cost": 1 if cost is None else 0,
        }
        table = LlmUsageMonthlyModel.__table__
        statement = insert(table).values(**row)
        summed = ("calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd", "calls_without_cost")
        session.execute(statement.on_conflict_do_update(
            index_elements=["tenant_id", "month", "operation", "model", "key_source"],
            set_={name: table.c[name] + statement.excluded[name] for name in summed},
        ))
        session.commit()


async def record(*, operation: str, model: str, usage: Usage, key_source: str = "platform") -> None:
    """After an LLM call. A failure to record is logged, not raised: the
    call has happened and its result is the caller's."""
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return
    try:
        await asyncio.to_thread(_record, tenant_id, operation, model, usage, key_source)
    except Exception as exc:  # noqa: BLE001 -- recorded in the JSONL log regardless
        logger.warning("llm_usage_record_failed", extra={"tenant_id": tenant_id, "error": str(exc)})
