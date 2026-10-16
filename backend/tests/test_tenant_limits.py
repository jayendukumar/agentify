"""Epic 38, US38.9: fair-share jobs, per-tenant limits, and usage records.

A tenant's jobs get their turn while another tenant has thousands queued;
each tenant's LLM calls are recorded as its own rows, held to its requests
per minute, and refused once its monthly budget is spent."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import insert, select, text

from app.config import Settings, get_settings
from app.db.models import AuditEventModel, JobModel, LlmUsageEventModel, LlmUsageMonthlyModel
from app.db.session import get_owner_engine, get_session_factory
from app.ids import new_id
from app.jobs.worker import Worker, handler, run_pending
from app.llm import metering
from app.llm.client import LLMClient
from app.llm.exceptions import LLMBudgetError
from app.llm.types import ChatMessage, Usage
from app.tenancy import tenant_scope
from app.tenancy.limits import forget_cached_limits

from .conftest import TEST_TENANT_ID, make_tenant
from .test_batches import ROWS, _batch_url, _setup
from .test_operator_console import _local_operators, _operator, _platform_events  # noqa: F401

BIG = "ten_big"
PRICED = "qwen/qwen3.7-flash"  # $0.03 in, $0.13 out per 1M tokens


@pytest.fixture(autouse=True)
def _fresh_limits():
    forget_cached_limits()
    metering._announced.clear()
    metering._windows.clear()
    yield
    forget_cached_limits()


def _set_limits(tenant_id: str, **limits) -> None:
    with get_owner_engine().begin() as conn:
        sets = ", ".join(f"{name} = :{name}" for name in limits)
        conn.execute(text(f"UPDATE tenants SET {sets} WHERE id = :id"), {**limits, "id": tenant_id})
    forget_cached_limits()


def _queue(tenant_id: str, kind: str, count: int) -> list[str]:
    ids = [new_id("job") for _ in range(count)]
    with tenant_scope(tenant_id), get_session_factory()() as session:
        session.execute(insert(JobModel), [{"id": i, "kind": kind, "payload": {}, "status": "queued",
                                            "priority": 100, "max_attempts": 1} for i in ids])
        session.commit()
    return ids


def _statuses(tenant_id: str, ids: list[str]) -> dict[str, str]:
    with tenant_scope(tenant_id), get_session_factory()() as session:
        return dict(session.execute(select(JobModel.id, JobModel.status).where(JobModel.id.in_(ids))).all())


# ---- fair share --------------------------------------------------------------------


_gate: dict[str, asyncio.Event] = {}


@handler("test_fair_share")
async def _held(payload, session):
    """Runs until the test lets it finish, so its slot stays taken."""
    await _gate["open"].wait()
    return None


def _running(worker: Worker) -> dict[str, int]:
    """Jobs this worker is running now, per tenant."""
    counts = {t: sum(1 for task in tasks if not task.done()) for t, tasks in worker._by_tenant.items()}
    return {t: n for t, n in counts.items() if n}


async def _drain(worker: Worker) -> None:
    _gate["open"].set()
    tasks = [t for group in worker._running.values() for t in group]
    await asyncio.gather(*tasks)


@pytest.mark.parametrize("turn", [0, 1])
def test_a_small_tenants_job_runs_while_another_tenant_has_10000_queued(turn):
    """Whichever tenant the walk starts with, the one job gets a slot on the
    first tick: free capacity goes round the tenants a job at a time."""
    make_tenant(BIG, "big", ["big.test"])
    _queue(BIG, "test_fair_share", 10_000)
    mine = _queue(TEST_TENANT_ID, "test_fair_share", 1)

    async def scenario():
        _gate["open"] = asyncio.Event()
        worker = Worker()
        worker._turn = turn
        started = time.monotonic()
        await worker.tick()
        elapsed = time.monotonic() - started
        claimed = _running(worker)
        await _drain(worker)
        return elapsed, claimed

    elapsed, claimed = asyncio.run(scenario())
    assert elapsed < 5
    assert _statuses(TEST_TENANT_ID, mine) == {mine[0]: "succeeded"}
    # Two slots for the kind (JOB_CONCURRENCY): one each, not both to the big tenant.
    assert claimed == {TEST_TENANT_ID: 1, BIG: 1}


def test_the_tenant_with_fewer_jobs_running_gets_the_next_free_slot():
    make_tenant(BIG, "big", ["big.test"])
    _queue(BIG, "test_fair_share", 50)

    async def scenario():
        _gate["open"] = asyncio.Event()
        worker = Worker()
        await worker.tick()  # the big tenant alone: it takes both slots
        assert _running(worker) == {BIG: 2}
        mine = _queue(TEST_TENANT_ID, "test_fair_share", 1)
        await worker.tick()  # nothing free: nobody new starts
        assert _statuses(TEST_TENANT_ID, mine) == {mine[0]: "queued"}
        _gate["open"].set()
        await asyncio.gather(*[t for group in worker._running.values() for t in group])
        _gate["open"] = asyncio.Event()
        await worker.tick()
        claimed = _running(worker)
        await _drain(worker)
        return mine, claimed

    mine, claimed = asyncio.run(scenario())
    assert claimed == {TEST_TENANT_ID: 1, BIG: 1}
    assert _statuses(TEST_TENANT_ID, mine) == {mine[0]: "succeeded"}


def test_a_tenant_never_runs_more_jobs_at_once_than_its_limit():
    _set_limits(TEST_TENANT_ID, max_concurrent_jobs=1)
    jobs = _queue(TEST_TENANT_ID, "test_fair_share", 3)

    async def scenario():
        _gate["open"] = asyncio.Event()
        first, second = Worker(), Worker()  # two workers: the limit is counted in the database
        await first.tick()
        await second.tick()
        running = list(_statuses(TEST_TENANT_ID, jobs).values()).count("running")
        await _drain(first)
        await _drain(second)
        return running

    assert asyncio.run(scenario()) == 1
    assert list(_statuses(TEST_TENANT_ID, jobs).values()).count("queued") == 2


# ---- usage records -----------------------------------------------------------------


def _spend(tenant_id: str, *, input_tokens: int, output_tokens: int = 0, model: str = PRICED,
           operation: str = "test_op") -> None:
    usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens)
    with tenant_scope(tenant_id):
        asyncio.run(metering.record(operation=operation, model=model, usage=usage))


def _client_with_fake_provider() -> LLMClient:
    client = LLMClient(settings=Settings(_env_file=None, openrouter_api_key="sk-test", llm_model=PRICED))
    message = SimpleNamespace(content="hi", tool_calls=None)
    response = SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], model=PRICED,
                               usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500, total_tokens=1500))
    client._client.chat.completions.create = AsyncMock(return_value=response)
    return client


def test_every_llm_call_is_recorded_as_the_tenants_usage():
    make_tenant(BIG, "big", ["big.test"])
    client = _client_with_fake_provider()
    for _ in range(2):
        asyncio.run(client.complete([ChatMessage(role="user", content="hello")], operation="chat_edit"))
    assert client.usage_tracker.events()[0].tenant_id == TEST_TENANT_ID  # the JSONL line says whose

    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        events = list(session.scalars(select(LlmUsageEventModel)))
        month = session.scalars(select(LlmUsageMonthlyModel)).one()
    assert [(e.operation, e.total_tokens) for e in events] == [("chat_edit", 1500)] * 2
    assert (month.calls, month.input_tokens, month.output_tokens, month.calls_without_cost) == (2, 2000, 1000, 0)
    assert month.cost_usd == pytest.approx(2 * (1000 * 0.03 + 500 * 0.13) / 1_000_000)
    with tenant_scope(BIG), get_session_factory()() as session:
        assert session.scalars(select(LlmUsageEventModel)).all() == []


def test_calls_to_a_model_without_a_price_are_counted_not_costed():
    _spend(TEST_TENANT_ID, input_tokens=100, model="someone/unpriced")
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        month = session.scalars(select(LlmUsageMonthlyModel)).one()
    assert (month.calls, month.cost_usd, month.calls_without_cost) == (1, 0.0, 1)


# ---- the monthly budget ------------------------------------------------------------


def _audit(action: str) -> list[AuditEventModel]:
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        return list(session.scalars(select(AuditEventModel).where(AuditEventModel.action == action)))


def test_the_budget_warns_at_80_percent_and_stops_at_100():
    _set_limits(TEST_TENANT_ID, llm_monthly_budget_usd=1.0)
    client = _client_with_fake_provider()
    ask = lambda: asyncio.run(client.complete([ChatMessage(role="user", content="hi")], operation="op"))  # noqa: E731

    _spend(TEST_TENANT_ID, input_tokens=28_000_000)  # $0.84
    ask()
    ask()
    warnings = _audit("llm_budget_warning")
    assert len(warnings) == 1 and warnings[0].detail["budget_usd"] == 1.0  # once, not per call

    _spend(TEST_TENANT_ID, input_tokens=6_000_000)  # $1.02
    with pytest.raises(LLMBudgetError, match="used up"):
        ask()
    with pytest.raises(LLMBudgetError):
        ask()
    assert len(_audit("llm_budget_reached")) == 1
    assert client._client.chat.completions.create.await_count == 2  # refused calls never reach the provider


def test_another_tenants_spending_never_counts_against_this_one():
    make_tenant(BIG, "big", ["big.test"])
    _set_limits(TEST_TENANT_ID, llm_monthly_budget_usd=1.0)
    _spend(BIG, input_tokens=100_000_000)  # $3 for the big tenant
    client = _client_with_fake_provider()
    asyncio.run(client.complete([ChatMessage(role="user", content="hi")], operation="op"))


def test_a_batch_stops_when_the_tenants_month_is_spent(client, fake_llm, monkeypatch):
    process, _artifact, _dataset, scenario = _setup(client, fake_llm, ROWS)
    monkeypatch.setattr(get_settings(), "job_worker_enabled", True)  # queue only; we drive it
    batch = client.post(_batch_url(process["id"], f"/scenarios/{scenario['id']}/batches"), json={"tier": "full"}).json()
    _set_limits(TEST_TENANT_ID, llm_monthly_budget_usd=0.5)
    _spend(TEST_TENANT_ID, input_tokens=20_000_000)  # $0.60
    asyncio.run(run_pending(kinds=["twin_run"]))
    view = client.get(_batch_url(process["id"], f"/batches/{batch['id']}")).json()
    assert view["status"] == "stopped_budget"
    assert view["progress"]["cancelled"] == 4 and view["progress"]["passed"] == 0


# ---- requests per minute -----------------------------------------------------------


def test_requests_over_the_tenants_rate_wait_for_the_window(monkeypatch):
    _set_limits(TEST_TENANT_ID, llm_requests_per_minute=2)
    monkeypatch.setattr(metering, "_WINDOW_SECONDS", 0.5)

    async def three_calls():
        started = time.monotonic()
        for _ in range(3):
            await metering.admit()
        return time.monotonic() - started

    assert asyncio.run(three_calls()) >= 0.45  # the third waited for the first to leave the window

    make_tenant(BIG, "big", ["big.test"])  # no limit of its own, and its own window

    async def big_calls():
        started = time.monotonic()
        with tenant_scope(BIG):
            for _ in range(5):
                await metering.admit()
        return time.monotonic() - started

    assert asyncio.run(big_calls()) < 0.3


# ---- what admins and operators see -------------------------------------------------


def test_an_admin_sees_usage_and_cost_per_month_against_the_limits(client, viewer_client):
    _set_limits(TEST_TENANT_ID, llm_monthly_budget_usd=10.0, max_concurrent_jobs=4)
    _spend(TEST_TENANT_ID, input_tokens=1_000_000, operation="chat_edit")  # $0.03
    _spend(TEST_TENANT_ID, input_tokens=2_000_000, operation="bpmn_generation")  # $0.06
    _spend(TEST_TENANT_ID, input_tokens=10, model="someone/unpriced")
    _queue(TEST_TENANT_ID, "test_fair_share", 2)

    overview = client.get("/api/usage").json()
    assert overview["limits"] == {"max_concurrent_jobs": 4, "llm_requests_per_minute": None,
                                  "llm_monthly_budget_usd": 10.0}
    assert overview["budget"]["status"] == "ok" and overview["budget"]["warn_at_usd"] == 8.0
    assert overview["budget"]["spent_usd"] == pytest.approx(0.09)
    assert overview["jobs_queued"] == 2 and overview["jobs_running"] == 0
    [month] = overview["months"]
    assert month["month"] == overview["budget"]["month"]
    assert month["calls"] == 3 and month["calls_without_cost"] == 1
    assert [op["operation"] for op in month["operations"]][0] == "bpmn_generation"  # most expensive first
    assert viewer_client.get("/api/usage").status_code == 403


def test_an_operator_sets_a_tenants_limits_and_sees_its_cost_this_month(client):
    _spend(TEST_TENANT_ID, input_tokens=1_000_000)
    ops = _operator()
    [tenant] = ops.get("/api/operator/tenants").json()
    assert tenant["usage"]["llm_calls_this_month"] == 1
    assert tenant["usage"]["llm_cost_this_month_usd"] == pytest.approx(0.03)
    assert tenant["limits"] == {"max_concurrent_jobs": None, "llm_requests_per_minute": None,
                                "llm_monthly_budget_usd": None}

    changed = ops.put(f"/api/operator/tenants/{TEST_TENANT_ID}/limits",
                      json={"max_concurrent_jobs": 3, "llm_requests_per_minute": 60, "llm_monthly_budget_usd": 25})
    assert changed.status_code == 200, changed.text
    assert changed.json()["effective_limits"]["llm_monthly_budget_usd"] == 25
    [event] = _platform_events("tenant_limits_changed")
    assert event.tenant_id == TEST_TENANT_ID and event.detail["to"]["max_concurrent_jobs"] == 3
    assert client.get("/api/usage").json()["limits"]["llm_requests_per_minute"] == 60  # the tenant sees them
    # ... and can't change them: the route is the console's alone.
    assert client.put(f"/api/operator/tenants/{TEST_TENANT_ID}/limits", json={}).status_code == 404
    assert ops.put(f"/api/operator/tenants/{TEST_TENANT_ID}/limits", json={"max_concurrent_jobs": 0}).status_code == 422


def test_empty_limits_fall_back_to_the_deployments_defaults(monkeypatch):
    monkeypatch.setattr(get_settings(), "tenant_llm_monthly_budget_usd", 5.0)
    _set_limits(TEST_TENANT_ID, llm_requests_per_minute=30)
    from app.tenancy.limits import limits_for

    limits = limits_for(TEST_TENANT_ID)
    assert (limits.llm_monthly_budget_usd, limits.llm_requests_per_minute, limits.max_concurrent_jobs) == (5.0, 30, None)
