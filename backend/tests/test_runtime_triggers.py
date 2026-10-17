"""Epic 39, US39.4: ways to start an agent -- input checked against the
input schema, internal triggers through the job queue, runtime clients
limited to some deployments, and signed callbacks that retry."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_owner_engine, get_session_factory
from app.jobs.worker import run_pending
from app.main import app
from app.runtime import jobs
from app.runtime.inputs import expected_type

from .test_runtime import FakeSystems, _isolate, _live, _result, _run_jobs, _schema_tool, _start, systems  # noqa: F401 (fixtures)

CALLBACK = "https://hooks.loan.example/agent-done"


def _set_input_schema(deployment_id: str, fields: list[tuple[str, str]]) -> None:
    with get_owner_engine().begin() as conn:
        snapshot = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment_id})
        snapshot["definition"]["input_schema"] = [
            {"name": n, "source_or_destination": "loan system", "format": f} for n, f in fields]
        conn.execute(text("UPDATE agent_deployments SET snapshot = CAST(:s AS json) WHERE id = :i"),
                     {"s": json.dumps(snapshot), "i": deployment_id})


def _start_with_callback(token: str, deployment_id: str, key: str, url: str = CALLBACK):
    return TestClient(app).post(f"/runtime/v1/deployments/{deployment_id}/executions",
                                json={"input": {}, "callback_url": url},
                                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key})


def _run(kinds: list[str]) -> int:
    return asyncio.run(run_pending(kinds=kinds))


def _job(kind: str) -> tuple[str, int, str | None]:
    with get_owner_engine().connect() as conn:
        return tuple(conn.execute(text("SELECT status, attempts, last_error FROM jobs WHERE kind = :k"),
                                  {"k": kind}).one())


def _make_due(kind: str) -> None:
    with get_owner_engine().begin() as conn:
        conn.execute(text("UPDATE jobs SET run_after = now() - interval '1 second' WHERE kind = :k"), {"k": kind})


# ---- input ------------------------------------------------------------------------------


def test_formats_that_name_a_type_are_checked_and_others_take_anything():
    assert expected_type("number")[0] == "a number"
    assert expected_type("Date (YYYY-MM-DD)")[0].startswith("an ISO date")
    assert expected_type("list of line items")[0] == "a list"
    for free in ("currency code", "phone number", "PDF", ""):
        assert expected_type(free) is None


@pytest.mark.usefixtures("systems")
def test_input_that_doesnt_match_the_schema_gets_422(client, fake_llm):
    _, deployment, token = _live(client, fake_llm)
    _set_input_schema(deployment["id"], [("customerId", "string"), ("amount", "number"), ("dueOn", "date"),
                                         ("urgent", "boolean"), ("currency", "currency code")])
    refused = _start(token, deployment["id"], {"customerId": 7, "amount": "100", "dueOn": "next week",
                                               "urgent": None, "currency": 978})
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    for problem in ("'customerId' must be text", "'amount' must be a number", "'dueOn' must be an ISO date",
                    "missing input field 'urgent'"):
        assert problem in detail
    assert "currency" not in detail  # a free-text format takes any value
    assert not fake_llm.complete.call_args_list or all(
        c.kwargs.get("operation") != "runtime_execution" for c in fake_llm.complete.call_args_list)

    accepted = _start(token, deployment["id"], {"customerId": "C-1", "amount": 100, "dueOn": "2026-11-01",
                                                "urgent": False, "currency": "EUR"})
    assert accepted.status_code == 202, accepted.text


# ---- internal triggers ------------------------------------------------------------------


@pytest.mark.usefixtures("systems")
def test_an_internal_trigger_starts_a_case_through_the_queue(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    tool = _schema_tool(process_id, deployment["id"])
    fake_llm.complete.side_effect = [
        _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})]),
        _result(text_='{"summary": "Ada is low risk"}'),
    ]
    with get_session_factory()() as session:
        jobs.queue_submission(session, deployment["id"], data={}, idempotency_key="sweep-2026-10-17",
                              trigger="nightly-sweep")
        # Queued twice by a sweep that ran twice: one job, one case.
        jobs.queue_submission(session, deployment["id"], data={}, idempotency_key="sweep-2026-10-17",
                              trigger="nightly-sweep")
        session.commit()
    assert _run(["runtime_submit"]) == 1
    assert _run_jobs() == 1

    executions = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/executions").json()
    assert len(executions) == 1
    detail = client.get(f"/api/processes/{process_id}/executions/{executions[0]['id']}").json()
    assert detail["status"] == "COMPLETED" and detail["trigger"] == "nightly-sweep" and detail["client_id"] is None
    with get_owner_engine().connect() as conn:
        detail_ = conn.scalar(text("SELECT detail FROM audit_events WHERE action = 'runtime_execution_submitted'"))
    assert detail_["trigger"] == "nightly-sweep"

    # The same key again, later: the same case, nothing new runs.
    with get_session_factory()() as session:
        jobs.queue_submission(session, deployment["id"], data={}, idempotency_key="sweep-2026-10-17",
                              trigger="nightly-sweep")
        session.commit()
    _run(["runtime_submit"])
    assert len(client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/executions").json()) == 1


def test_an_internal_trigger_is_refused_like_the_api(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    client.post(f"/api/processes/{process_id}/deployments/{deployment['id']}/kill", json={"reason": "Incident 7"})
    with get_session_factory()() as session:
        jobs.queue_submission(session, deployment["id"], data={}, idempotency_key="k1", trigger="nightly-sweep")
        session.commit()
    _run(["runtime_submit"])
    assert _job("runtime_submit")[0] == "succeeded"
    with get_owner_engine().connect() as conn:
        outcome, detail = conn.execute(text("SELECT outcome, detail FROM audit_events "
                                            "WHERE action = 'runtime_execution_refused'")).one()
        assert conn.scalar(text("SELECT count(*) FROM agent_executions")) == 0
    assert outcome == "refused" and detail["status"] == 503 and "Incident 7" in detail["reason"]


def test_an_internal_trigger_at_a_cap_retries_later(client, fake_llm):
    process_id, deployment, token = _live(client, fake_llm)
    settings = client.put(f"/api/processes/{process_id}/deployments/{deployment['id']}/settings",
                          json={"limits": {"executions_per_minute": 1}})
    assert settings.status_code == 200, settings.text
    assert _start(token, deployment["id"], {}).status_code == 202
    with get_session_factory()() as session:
        jobs.queue_submission(session, deployment["id"], data={}, idempotency_key="k2", trigger="nightly-sweep")
        session.commit()
    _run(["runtime_submit"])
    status, attempts, error = _job("runtime_submit")
    assert status == "queued" and attempts == 1 and "limit" in error


# ---- clients ----------------------------------------------------------------------------


def test_a_client_limited_to_other_deployments_gets_404(client, fake_llm):
    _, deployment, _ = _live(client, fake_llm)
    unknown = client.post("/api/runtime/clients", json={"label": "Loans", "deployment_ids": ["dep_nope"]})
    assert unknown.status_code == 422

    mine = client.post("/api/runtime/clients", json={"label": "Loans", "deployment_ids": [deployment["id"]]}).json()
    assert mine["deployment_ids"] == [deployment["id"]]
    assert _start(mine["token"], deployment["id"], {}).status_code == 202
    with get_owner_engine().begin() as conn:
        conn.execute(text("UPDATE runtime_clients SET deployment_ids = CAST('[\"dep_other\"]' AS json) "
                          "WHERE id = :i"), {"i": mine["id"]})
    assert _start(mine["token"], deployment["id"], {}, key="case-2").status_code == 404
    assert "signing_secret" not in client.get("/api/runtime/clients").json()[0]


# ---- callbacks --------------------------------------------------------------------------


@pytest.fixture
def callback_host(monkeypatch):
    monkeypatch.setattr(get_settings(), "runtime_callback_hosts", "hooks.loan.example")


def test_a_finished_case_calls_back_signed_and_retries(client, fake_llm, monkeypatch, callback_host):
    answers = [500, 204]
    fake = FakeSystems()
    real_handler = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hooks.loan.example":
            fake.requests.append(request)
            return httpx.Response(answers.pop(0))
        return real_handler(request)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    _, deployment, _ = _live(client, fake_llm)
    created = client.post("/api/runtime/clients", json={"label": "Loans"}).json()
    fake_llm.complete.side_effect = [_result(text_='{"summary": "nothing to do"}')]
    started = _start_with_callback(created["token"], deployment["id"], "cb-1")
    assert started.status_code == 202, started.text
    _run_jobs()

    # The caller is down at first: the callback is retried, not dropped.
    _run(["runtime_callback"])
    status, attempts, error = _job("runtime_callback")
    assert status == "queued" and attempts == 1 and "500" in error
    _make_due("runtime_callback")
    _run(["runtime_callback"])
    assert _job("runtime_callback")[0] == "succeeded"

    sent = [r for r in fake.requests if r.url.host == "hooks.loan.example"]
    assert len(sent) == 2
    body = sent[1].content
    payload = json.loads(body)
    assert payload["completed_at"].endswith("+00:00")
    assert {k: v for k, v in payload.items() if k != "completed_at"} == {
        "execution_id": started.json()["id"], "deployment_id": deployment["id"], "status": "COMPLETED",
        "final_output": {"summary": "nothing to do"}}
    t, v1 = (part.split("=", 1)[1] for part in sent[1].headers["Axyntro-Signature"].split(","))
    expected = hmac.new(created["signing_secret"].encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(v1, expected)


def test_a_case_killed_before_it_started_calls_back(client, fake_llm, callback_host):
    process_id, deployment, token = _live(client, fake_llm)
    started = _start_with_callback(token, deployment["id"], "cb-2")
    assert started.status_code == 202, started.text
    client.post(f"/api/processes/{process_id}/deployments/{deployment['id']}/kill", json={"reason": "Incident 9"})
    with get_owner_engine().connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM jobs WHERE kind = 'runtime_callback'")) == 1


def test_a_callback_host_not_allowed_is_refused(client, fake_llm):
    _, deployment, token = _live(client, fake_llm)
    refused = _start_with_callback(token, deployment["id"], "cb-3", url="https://evil.example/x")
    assert refused.status_code == 422 and "RUNTIME_CALLBACK_HOSTS" in refused.text
