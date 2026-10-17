"""Epic 39, build step 3: the review inbox (US39.10), limits across
executions and circuit breakers (US39.9), and automatic pause (US39.12)."""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import get_owner_engine, get_session_factory
from app.main import app
from app.runtime import reviews
from app.schemas.agents import ArgumentRule, ToolContract
from app.twin.gateway import check_argument_rules

from .conftest import sign_in
from . import test_runtime as rt
from .test_runtime import _live, _result, _run_jobs, _schema_tool, _start

# The step 2 tests' fixtures: a secret for the binding, and the fake systems.
_isolate = rt._isolate
systems = rt.systems


def _snapshot_edit(deployment_id: str, edit) -> None:
    with get_owner_engine().begin() as conn:
        snapshot = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment_id})
        edit(snapshot["definition"])
        conn.execute(text("UPDATE agent_deployments SET snapshot = CAST(:s AS json) WHERE id = :i"),
                     {"s": json.dumps(snapshot), "i": deployment_id})


def _sql(statement: str, **params):
    with get_owner_engine().begin() as conn:
        return conn.execute(text(statement), params)


def _reviewer(name="inbox-approver", roles=("approver",)) -> TestClient:
    c = TestClient(app)
    sign_in(c, name, list(roles))
    return c


def _authority(client, role: str, limit: float | None) -> None:
    created = client.post("/api/organization/entities", json={
        "kind": "approval_authority", "name": f"{role}: payments",
        "data": {"role_name": role, "decision_type": "payment", "limit_amount": limit, "currency": "EUR"}})
    assert created.status_code == 201, created.text


def _with_checkpoint(definition):
    definition["human_checkpoint"] = "review_before_action"


def _ask(amount: float):
    return _result(calls=[("request_human_decision", {"summary": f"Pay {amount} EUR?",
                                                       "proposed_action": {"amount": amount}})])


# ---- US39.10: the review inbox -------------------------------------------------------------


def test_a_checkpoint_waits_in_the_inbox_and_a_decision_resumes_it(client, fake_llm, systems):
    _authority(client, "Team Lead", 1000)
    _authority(client, "Finance Manager", 10000)
    process_id, deployment, token = _live(client, fake_llm)
    _snapshot_edit(deployment["id"], _with_checkpoint)
    fake_llm.complete.side_effect = [_ask(500), _result(text_='{"summary": "paid"}')]
    caller = TestClient(app)
    started = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                          json={"input": {}, "requested_by": "pat"},
                          headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "chk-1"}).json()
    _run_jobs()

    approver = _reviewer()
    inbox = approver.get("/api/runtime/inbox").json()
    assert len(inbox) == 1
    task = inbox[0]
    assert task["kind"] == "checkpoint" and task["summary"] == "Pay 500 EUR?"
    assert task["proposed_action"] == {"amount": 500} and task["assignee_role"] == "Team Lead"
    url = f"/api/processes/{process_id}/review-tasks/{task['id']}"

    # Whoever the case is for can't review it; a rejection needs a reason.
    assert _reviewer("pat").post(f"{url}/decide", json={"decision": "approve"}).status_code == 403
    assert approver.post(f"{url}/decide", json={"decision": "reject"}).status_code == 422
    # Someone without the review permission can't.
    assert _reviewer("inbox-analyst", ("analyst",)).post(f"{url}/decide", json={"decision": "approve"}).status_code == 403

    decided = approver.post(f"{url}/decide", json={"decision": "approve", "comment": "Within budget"})
    assert decided.status_code == 200 and decided.json()["status"] == "decided"
    assert _run_jobs() == 1
    detail = client.get(f"/api/processes/{process_id}/executions/{started['id']}").json()
    assert detail["status"] == "COMPLETED" and detail["final_output"] == {"summary": "paid"}
    approval = next(s for s in detail["steps"] if s["type"] == "HUMAN_APPROVED")
    assert approval["reason"] == "Approved by inbox-approver: Within budget"
    assert [s["type"] for s in detail["steps"]].count("HUMAN_APPROVAL_REQUESTED") == 1
    with get_owner_engine().connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM audit_events WHERE action = 'runtime_review_decided'")) == 1
    assert approver.get("/api/runtime/inbox").json() == []


def test_an_amount_over_a_limit_goes_to_the_level_that_may_approve_it(client, fake_llm, systems):
    _authority(client, "Team Lead", 1000)
    _authority(client, "Finance Manager", 10000)
    process_id, deployment, token = _live(client, fake_llm)
    del process_id
    _snapshot_edit(deployment["id"], _with_checkpoint)
    fake_llm.complete.side_effect = [_ask(5000)]
    _start(token, deployment["id"], {})
    _run_jobs()
    assert _reviewer().get("/api/runtime/inbox").json()[0]["assignee_role"] == "Finance Manager"


def test_an_overdue_review_moves_up_and_tells_the_owners(client, fake_llm, systems):
    _authority(client, "Team Lead", 1000)
    _authority(client, "Finance Manager", 10000)
    process_id, deployment, token = _live(client, fake_llm)
    _snapshot_edit(deployment["id"], _with_checkpoint)
    fake_llm.complete.side_effect = [_ask(100)]
    _start(token, deployment["id"], {})
    _run_jobs()
    _sql("UPDATE review_tasks SET due_at = now() - interval '1 minute'")
    with get_session_factory()() as session:
        assert reviews.escalate_overdue(session) == 1
        session.commit()
    task = _reviewer().get("/api/runtime/inbox").json()[0]
    assert task["level"] == 1 and task["assignee_role"] == "Finance Manager" and not task["overdue"]
    notes = client.get("/api/runtime/notifications").json()  # the creator owns the deployment
    assert notes and notes[0]["kind"] == "review_overdue" and "now with Finance Manager" in notes[0]["message"]
    del process_id


def test_an_escalated_case_is_resolved_by_hand_and_feeds_a_dataset(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    _snapshot_edit(deployment["id"], lambda d: d.update(
        guardrails={**d["guardrails"], "max_steps": 1}, escalation_policy={"escalate_when": ["stuck"]}))
    tool = _schema_tool(process_id, deployment["id"])
    fake_llm.complete.side_effect = [_result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})])]
    execution_id = _start(token, deployment["id"], {"customerId": "C-1"}).json()["id"]
    _run_jobs()
    approver = _reviewer()
    task = approver.get("/api/runtime/inbox").json()[0]
    assert task["kind"] == "escalation" and "max_steps" in task["summary"]
    url = f"/api/processes/{process_id}/review-tasks/{task['id']}"
    assert approver.post(f"{url}/decide", json={"decision": "approve"}).status_code == 409
    resolved = approver.post(f"{url}/resolve", json={"outcome": {"decision": "approve", "riskRating": "low"},
                                                     "comment": "Checked by phone"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    detail = client.get(f"/api/processes/{process_id}/executions/{execution_id}").json()
    assert detail["status"] == "ESCALATED" and detail["resolution"]["outcome"]["riskRating"] == "low"

    dataset = client.post(f"/api/processes/{process_id}/datasets", json={"name": "Live cases"}).json()
    added = client.post(f"{url}/to-dataset", json={"dataset_id": dataset["id"]})
    assert added.status_code == 200, added.text
    rows = client.get(f"/api/processes/{process_id}/datasets/{dataset['id']}/rows").json()
    rows = rows["rows"] if isinstance(rows, dict) else rows
    assert rows[0]["inputs"] == {"customerId": "C-1"}
    assert rows[0]["labels"] == {"outputs": {"riskRating": "low"}, "decision": "approve"}
    assert "from-production" in rows[0]["tags"]


def test_killing_a_deployment_cancels_its_open_reviews(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    _snapshot_edit(deployment["id"], _with_checkpoint)
    fake_llm.complete.side_effect = [_ask(10)]
    _start(token, deployment["id"], {})
    _run_jobs()
    client.post(f"/api/processes/{process_id}/deployments/{deployment['id']}/kill", json={"reason": "Bad prompt"})
    assert _reviewer().get("/api/runtime/inbox").json() == []


# ---- US39.9: limits, circuits, argument rules -------------------------------------------------


def test_defaults_come_from_the_twin_and_caps_refuse_with_429(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    detail = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()
    assert detail["limits"]["executions_per_minute"] >= 10 and "basis" in detail["limits"]
    assert detail["pause_rules"]["errors"]["enabled"] is True

    changed = client.put(f"/api/processes/{process_id}/deployments/{deployment['id']}/settings",
                         json={"limits": {"executions_per_minute": 1}})
    assert changed.status_code == 200 and changed.json()["limits"]["executions_per_minute"] == 1
    assert _start(token, deployment["id"], {}, key="one").status_code == 202
    second = _start(token, deployment["id"], {}, key="two")
    assert second.status_code == 429 and "1 cases in the last minute" in second.json()["detail"]
    # Only an admin changes them.
    architect = _reviewer("limits-architect", ("architect",))
    assert architect.put(f"/api/processes/{process_id}/deployments/{deployment['id']}/settings",
                         json={"limits": {"executions_per_minute": 100}}).status_code == 403


def test_a_failing_system_opens_its_circuit_and_a_probe_closes_it(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    client.put(f"/api/processes/{process_id}/deployments/{deployment['id']}/settings",
               json={"pause_rules": {"circuit": {"failures_to_open": 2, "window": 20, "min_calls": 10,
                                                 "max_error_rate": 0.5, "cooldown_seconds": 60},
                                     "errors": {"enabled": False}}})
    tool = _schema_tool(process_id, deployment["id"])
    lookup = (tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})
    failing = {"on": True}
    real_handler = systems.handler
    systems.handler = lambda r: httpx.Response(503, json={"down": True}) if failing["on"] and r.url.host == "api.bank.example" else real_handler(r)
    for i in range(2):
        fake_llm.complete.side_effect = [_result(calls=[lookup]), _result(text_='{"summary": "down"}')]
        _start(token, deployment["id"], {}, key=f"fail-{i}")
        _run_jobs()
    circuit = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()["circuits"][0]
    assert circuit["state"] == "open" and "2 failures in a row" in circuit["reason"]

    calls_before = len(systems.requests)
    fake_llm.complete.side_effect = [_result(calls=[lookup])]
    blocked = _start(token, deployment["id"], {}, key="blocked").json()["id"]
    _run_jobs()
    detail = client.get(f"/api/processes/{process_id}/executions/{blocked}").json()
    assert detail["status"] == "ESCALATED" and "circuit is open" in detail["deviations"][-1]["reason"]
    assert len(systems.requests) == calls_before  # not called
    assert _reviewer().get("/api/runtime/inbox").json()[0]["kind"] == "escalation"

    # After the cool-down, the next call is the probe.
    _sql("UPDATE tool_circuits SET opened_at = now() - interval '2 minutes'")
    failing["on"] = False
    fake_llm.complete.side_effect = [_result(calls=[lookup]), _result(text_='{"summary": "back"}')]
    _start(token, deployment["id"], {}, key="probe")
    _run_jobs()
    circuit = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()["circuits"][0]
    assert circuit["state"] == "closed"


def test_argument_rules_bound_a_call_in_the_twin_and_production():
    contract = ToolContract(system_name="ERP", resource="invoice", action="update", argument_rules=[
        ArgumentRule(field="params.body.amount", operator="le", value=10000, operation="postInvoice"),
        ArgumentRule(field="params.body.currency", operator="in", value=["EUR", "USD"], operation="postInvoice"),
    ])
    ok = {"operation": "postInvoice", "params": {"body": {"amount": 900, "currency": "EUR"}}}
    assert check_argument_rules(contract, ok).allowed
    too_much = {"operation": "postInvoice", "params": {"body": {"amount": 20000, "currency": "EUR"}}}
    denied = check_argument_rules(contract, too_much)
    assert not denied.allowed and "params.body.amount le 10000" in denied.reason
    missing = check_argument_rules(contract, {"operation": "postInvoice", "params": {"body": {"currency": "EUR"}}})
    assert not missing.allowed and "is missing" in missing.reason
    other_operation = check_argument_rules(contract, {"operation": "getInvoice", "params": {}})
    assert other_operation.allowed
    wrong_type = check_argument_rules(contract, {"operation": "postInvoice", "params": {"body": {"amount": "lots",
                                                                                                 "currency": "EUR"}}})
    assert not wrong_type.allowed


# ---- US39.12: automatic pause --------------------------------------------------------------------


def test_failing_cases_pause_the_agent_and_resuming_needs_a_comment(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    base = f"/api/processes/{process_id}/deployments/{deployment['id']}"
    client.put(f"{base}/settings", json={"pause_rules": {"errors": {"enabled": True, "max_rate": 0.4, "window": 20,
                                                                    "min_cases": 2}}})
    for i in range(2):
        fake_llm.complete.side_effect = [_result(text_="not json"), _result(text_="still not json")]
        _start(token, deployment["id"], {}, key=f"bad-{i}")
        _run_jobs()
    paused = client.get(base).json()
    assert paused["status"] == "paused" and "2 of the last 2 cases failed" in paused["pause_reason"]
    notes = client.get("/api/runtime/notifications").json()
    assert notes[0]["kind"] == "deployment_paused" and "failed" in notes[0]["message"]

    refused = _start(token, deployment["id"], {}, key="while-paused")
    assert refused.status_code == 503 and "paused" in refused.json()["detail"]

    # Queue instead of refusing: the case waits for the resume.
    client.put(f"{base}/settings", json={"when_paused": "queue"})
    held = _start(token, deployment["id"], {}, key="held")
    assert held.status_code == 202
    _run_jobs()
    with get_owner_engine().connect() as conn:
        assert conn.scalar(text("SELECT status FROM agent_executions WHERE id = :i"), {"i": held.json()["id"]}) == "QUEUED"

    assert client.post(f"{base}/resume", json={"comment": ""}).status_code == 422
    resumed = client.post(f"{base}/resume", json={"comment": "Prompt fixed upstream"})
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"
    _sql("UPDATE jobs SET run_after = now() WHERE kind = 'runtime_execution' AND status = 'queued'")
    fake_llm.complete.side_effect = [_result(text_='{"summary": "fine"}')]
    _run_jobs()
    with get_owner_engine().connect() as conn:
        assert conn.scalar(text("SELECT status FROM agent_executions WHERE id = :i"), {"i": held.json()["id"]}) == "COMPLETED"


def test_rejected_reviews_pause_the_agent(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    base = f"/api/processes/{process_id}/deployments/{deployment['id']}"
    client.put(f"{base}/settings", json={"pause_rules": {"rejections": {"enabled": True, "max_rate": 0.5,
                                                                        "window": 20, "min_decisions": 2}}})
    _snapshot_edit(deployment["id"], _with_checkpoint)
    approver = _reviewer()
    for i in range(2):
        fake_llm.complete.side_effect = [_ask(10), _result(text_='{"summary": "not paid"}')]
        _start(token, deployment["id"], {}, key=f"ask-{i}")
        _run_jobs()
        task = approver.get("/api/runtime/inbox").json()[0]
        approver.post(f"/api/processes/{process_id}/review-tasks/{task['id']}/decide",
                      json={"decision": "reject", "comment": "Wrong payee"})
        _run_jobs()
    detail = client.get(base).json()
    assert detail["status"] == "paused" and "rejected 2 of the last 2" in detail["pause_reason"]


def test_a_manual_pause_lets_running_cases_finish(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    base = f"/api/processes/{process_id}/deployments/{deployment['id']}"
    assert client.post(f"{base}/pause", json={"reason": "Month-end freeze"}).json()["status"] == "paused"
    assert _start(token, deployment["id"], {}).status_code == 503
    assert client.post(f"{base}/pause", json={"reason": "again"}).status_code == 409


def test_another_tenants_inbox_never_shows_these_tasks(client, fake_llm, systems):
    from .conftest import ALL_ROLES, make_tenant

    process_id, deployment, token = _live(client, fake_llm)
    del process_id
    _snapshot_edit(deployment["id"], _with_checkpoint)
    fake_llm.complete.side_effect = [_ask(10)]
    _start(token, deployment["id"], {})
    _run_jobs()
    assert len(_reviewer().get("/api/runtime/inbox").json()) == 1
    make_tenant("ten_inbox_b", "inboxb", ["tenant-inboxb.test"])
    other = TestClient(app, base_url="http://tenant-inboxb.test")
    sign_in(other, "b-approver", ALL_ROLES)
    assert other.get("/api/runtime/inbox").json() == []
    assert other.get("/api/runtime/notifications").json() == []
