"""Epic 39, US39.16: an agent started by a signed event from the tenant's
own systems."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import get_owner_engine
from app.main import app
from app.runtime.events import signature

from .test_runtime import FakeSystems, _isolate, _live, _result, _run_jobs, _schema_tool, systems  # noqa: F401
from .test_runtime_triggers import _set_input_schema

SECRET = "loan-events-signing-secret"


@pytest.fixture(autouse=True)
def _event_secret(monkeypatch):
    monkeypatch.setenv("TENANT_TEST_LOAN_EVENTS", SECRET)


def _subscribe(client, process_id, deployment_id, **overrides):
    body = {"label": "Loan system events", "secret_ref": "secret://tenant/loan_events",
            "input_mapping": {"customerId": "data.applicant.id"}, "event_filter": {"type": "loan.submitted"},
            **overrides}
    return client.post(f"/api/processes/{process_id}/deployments/{deployment_id}/event-subscriptions", json=body)


def _send(url, event, *, secret=SECRET, timestamp=None, headers=None):
    body = json.dumps(event).encode()
    ts = str(int(timestamp if timestamp is not None else time.time()))
    sent = {"X-Timestamp": ts, "X-Signature": "sha256=" + signature(secret, ts, body),
            "Content-Type": "application/json"}
    sent.update(headers or {})
    return TestClient(app).post(url, content=body, headers=sent)


def _event(event_id="evt_1", customer="C-1", kind="loan.submitted"):
    return {"id": event_id, "type": kind, "data": {"applicant": {"id": customer}}}


@pytest.fixture
def subscribed(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    _set_input_schema(deployment["id"], [("customerId", "string")])
    created = _subscribe(client, process_id, deployment["id"])
    assert created.status_code == 201, created.text
    return process_id, deployment, created.json()


def test_a_subscription_is_checked_before_it_is_saved(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    _set_input_schema(deployment["id"], [("customerId", "string"), ("amount", "number")])
    for overrides, why in (({"secret_ref": SECRET}, "not the secret itself"),
                           ({"input_mapping": {"customerId": "data.id", "colour": "x"}}, "doesn't take: colour"),
                           ({"input_mapping": {"customerId": "data.id"}}, "leaves out input fields: amount")):
        refused = _subscribe(client, process_id, deployment["id"], **overrides)
        assert refused.status_code == 422 and why in refused.text, refused.text


@pytest.mark.usefixtures("systems")
def test_a_signed_event_starts_the_agent_once(client, fake_llm, subscribed):
    process_id, deployment, sub = subscribed
    assert sub["url"] == f"/runtime/v1/events/{sub['id']}"
    started = _send(sub["url"], _event())
    assert started.status_code == 202, started.text
    execution_id = started.json()["id"]
    again = _send(sub["url"], _event())  # the sender retried the delivery
    assert again.status_code == 200 and again.json()["id"] == execution_id

    tool = _schema_tool(process_id, deployment["id"])
    fake_llm.complete.side_effect = [
        _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})]),
        _result(text_='{"summary": "Ada is low risk"}'),
    ]
    _run_jobs()
    detail = client.get(f"/api/processes/{process_id}/executions/{execution_id}").json()
    assert detail["status"] == "COMPLETED" and detail["trigger"] == "event"
    assert detail["input"] == {"customerId": "C-1"} and detail["client_id"] is None
    listed = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/event-subscriptions").json()
    assert listed[0]["last_event_at"] is not None


def test_unsigned_forged_or_replayed_events_are_refused(subscribed):
    _, _, sub = subscribed
    url = sub["url"]
    assert _send(url, _event(), secret="someone-else").status_code == 401
    old = _send(url, _event(), timestamp=time.time() - 600)
    assert old.status_code == 401 and "5-minute window" in old.text
    unsigned = TestClient(app).post(url, json=_event())
    assert unsigned.status_code == 401 and "X-Timestamp" in unsigned.text
    # A body changed after signing.
    body = json.dumps(_event()).encode()
    ts = str(int(time.time()))
    tampered = TestClient(app).post(url, content=body.replace(b"C-1", b"C-2"),
                                    headers={"X-Timestamp": ts, "X-Signature": "sha256=" + signature(SECRET, ts, body)})
    assert tampered.status_code == 401
    with get_owner_engine().connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM agent_executions")) == 0


def test_filtered_unmappable_and_revoked_events(client, subscribed, monkeypatch):
    process_id, deployment, sub = subscribed
    ignored = _send(sub["url"], _event(kind="loan.withdrawn"))
    assert ignored.status_code == 200 and "loan.withdrawn" in ignored.json()["ignored"]
    no_id = _send(sub["url"], {"type": "loan.submitted", "data": {"applicant": {"id": "C-1"}}})
    assert no_id.status_code == 422 and "no id" in no_id.text
    unmapped = _send(sub["url"], {"id": "evt_9", "type": "loan.submitted", "data": {}})
    assert unmapped.status_code == 422 and "customerId" in unmapped.text
    not_object = _send(sub["url"], ["loan.submitted"])
    assert not_object.status_code == 400 and "JSON object" in not_object.text

    monkeypatch.delenv("TENANT_TEST_LOAN_EVENTS")
    assert _send(sub["url"], _event("evt_2")).status_code == 503
    monkeypatch.setenv("TENANT_TEST_LOAN_EVENTS", SECRET)
    revoked = client.post(f"/api/processes/{process_id}/deployments/{deployment['id']}"
                          f"/event-subscriptions/{sub['id']}/revoke")
    assert revoked.status_code == 200 and revoked.json()["revoked_at"]
    assert _send(sub["url"], _event("evt_3")).status_code == 404
    assert TestClient(app).post("/runtime/v1/events/evs_nowhere", json={}).status_code == 404
