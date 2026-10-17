"""Epic 39, build step 4: shadow, canary and rollback (US39.13), live
metrics, alerts and traces (US39.14), and drift against the twin (US39.15)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import AgentExecutionModel, ExecutionStepModel, ReviewTaskModel, RuntimeNotificationModel
from app.db.session import get_owner_engine, get_session_factory
from app.jobs.worker import run_pending
from app.main import app
from app.runtime import drift
from app.runtime.jobs import route
from app.tenancy.context import tenant_scope

from .conftest import TEST_TENANT_ID, sign_in
from .test_api_blueprint import approve_agent_as_other_user
from .test_runtime import (  # noqa: F401 (fixtures)
    FakeSystems, _bind, _deploy, _isolate, _live, _result, _run_jobs, _schema_tool, _start, systems)

HOOK = "https://hooks.ops.example/alert"
COLLECTOR = "https://otel.ops.example:4318"


def _approver() -> TestClient:
    approver = TestClient(app)
    sign_in(approver, "runtime-approver", ["approver", "process_owner"])
    return approver


def _activate(process_id, deployment_id, **body):
    return _approver().post(f"/api/processes/{process_id}/deployments/{deployment_id}/activate", json=body)


def _new_version(client, process_id) -> dict:
    """The agent regenerated and approved: v2 of the same agent."""
    artifact = client.post(f"/api/processes/{process_id}/blueprint/nodes/Task_a/agent-artifact").json()
    approve_agent_as_other_user(process_id, artifact["id"])
    deployment = _deploy(client, process_id, artifact["id"])
    assert _bind(client, process_id, deployment).status_code == 200
    return deployment


def _writer_llm(fake_llm, process_id, deployment_id, *, decision="approve"):
    """Looks the customer up, opens an account, then answers -- for every
    case, in any order."""
    tool = _schema_tool(process_id, deployment_id)

    def respond(messages, **kwargs):
        results = sum(1 for m in messages if getattr(m, "role", None) == "tool")
        if results == 0:
            return _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})])
        if results == 1:
            return _result(calls=[(tool, {"operation": _post_operation(deployment_id),
                                          "params": {"body": {"customerId": "C-1"}}})])
        return _result(text_=json.dumps({"accountId": "ACC-9", "decision": decision}))

    fake_llm.complete.side_effect = respond


def _reader_llm(fake_llm, process_id, deployment_id, *, fail_on: set[str] | None = None):
    tool = _schema_tool(process_id, deployment_id)

    def respond(messages, **kwargs):
        text_ = " ".join(str(getattr(m, "content", "")) for m in messages)
        if fail_on and any(marker in text_ for marker in fail_on):
            raise RuntimeError("model unavailable")
        if not any(getattr(m, "role", None) == "tool" for m in messages):
            return _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})])
        return _result(text_='{"summary": "Ada is low risk"}')

    fake_llm.complete.side_effect = respond


def _post_operation(deployment_id) -> str:
    with get_owner_engine().connect() as conn:
        snapshot = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment_id})
    ops = next(iter(snapshot["tool_schemas"].values()))["api_operations"]
    return next(op for op, shape in ops.items() if shape["method"] == "POST")


def _executions(deployment_id) -> list[dict]:
    with get_owner_engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            "SELECT id, status, shadow_of, idempotency_key, deployment_id FROM agent_executions "
            "WHERE deployment_id = :d ORDER BY created_at"), {"d": deployment_id})]


def _deployment(client, process_id, deployment_id) -> dict:
    return client.get(f"/api/processes/{process_id}/deployments/{deployment_id}").json()


def _run(kinds: list[str]) -> int:
    return asyncio.run(run_pending(kinds=kinds))


def _posts(systems) -> list:
    return [r for r in systems.requests if r.url.host == "api.bank.example" and r.method == "POST"]


# ---- US39.13: shadow ----------------------------------------------------------------------


@pytest.mark.usefixtures("systems")
def test_a_shadow_version_sees_live_cases_and_sends_nothing_that_changes_data(client, fake_llm, systems):
    process_id, v1, token = _live(client, fake_llm, writes=True)
    v2 = _new_version(client, process_id)
    refused = _activate(process_id, v2["id"])
    assert refused.status_code == 409 and "rollout 'shadow'" in refused.text
    shadowed = _activate(process_id, v2["id"], rollout="shadow")
    assert shadowed.status_code == 200, shadowed.text
    assert shadowed.json()["status"] == "shadow" and shadowed.json()["previous_deployment_id"] == v1["id"]

    _writer_llm(fake_llm, process_id, v1["id"])
    started = _start(token, v1["id"], {"customerId": "C-1"})
    assert started.status_code == 202
    assert _run_jobs() == 2
    live, = _executions(v1["id"])
    copy, = _executions(v2["id"])
    assert copy["shadow_of"] == live["id"] and copy["status"] == live["status"] == "COMPLETED"
    # The live case opened the account; the shadow copy only recorded it.
    assert len(_posts(systems)) == 1
    # Its lookup went to the real system, though the contract may also write.
    lookups = [r for r in systems.requests if r.url.host == "api.bank.example" and r.method == "GET"]
    assert len(lookups) == 2
    with get_owner_engine().connect() as conn:
        steps = conn.scalars(text("SELECT step FROM execution_steps WHERE execution_id = :e AND type = 'TOOL_EXECUTED' "
                                  "ORDER BY seq"), {"e": copy["id"]}).all()
        assert "shadow" not in steps[0]["result"]
        assert steps[1]["result"]["shadow"] is True and steps[1]["result"]["sent"] is False
        assert conn.scalar(text("SELECT count(*) FROM review_tasks WHERE execution_id = :e"), {"e": copy["id"]}) == 0

    # The loan system reports what really happened; the comparison pairs the two.
    reported = TestClient(app).post(f"/runtime/v1/executions/{live['id']}/outcome",
                                    json={"outcome": {"decision": "approve"}},
                                    headers={"Authorization": f"Bearer {token}"})
    assert reported.status_code == 200
    comparison = client.get(f"/api/processes/{process_id}/deployments/{v2['id']}/comparison").json()
    assert comparison["stage"] == "shadow" and comparison["against"]["deployment_id"] == v1["id"]
    paired = comparison["paired"]
    assert paired["pairs"] == 1 and paired["agreement"]["rate"] == 1.0 and paired["with_known_outcome"] == 1
    assert "same decision 100% of the time" in comparison["reading"]

    # Promoting needs someone other than who asked for the deployment.
    assert client.post(f"/api/processes/{process_id}/deployments/{v2['id']}/promote",
                       json={"share": 25}).status_code == 403
    canary = _approver().post(f"/api/processes/{process_id}/deployments/{v2['id']}/promote", json={"share": 25})
    assert canary.status_code == 200, canary.text
    assert canary.json()["status"] == "active" and canary.json()["traffic_share"] == 25
    full = _approver().post(f"/api/processes/{process_id}/deployments/{v2['id']}/promote", json={"share": 100})
    assert full.json()["traffic_share"] is None
    assert _deployment(client, process_id, v1["id"])["status"] == "retired"


def test_a_first_version_in_shadow_takes_cases_itself(client, fake_llm, systems):
    from .test_runtime import _approved_agent

    process_id, artifact_v1 = _approved_agent(client, fake_llm, writes=True)
    deployment = _deploy(client, process_id, artifact_v1["id"])
    _bind(client, process_id, deployment)
    assert _activate(process_id, deployment["id"], rollout="shadow").status_code == 200
    token = client.post("/api/runtime/clients", json={"label": "Loan system"}).json()["token"]
    _writer_llm(fake_llm, process_id, deployment["id"], decision="reject")
    started = _start(token, deployment["id"], {})
    assert started.status_code == 202
    _run_jobs()
    assert _posts(systems) == []
    TestClient(app).post(f"/runtime/v1/executions/{started.json()['id']}/outcome",
                         json={"outcome": {"decision": "approve"}}, headers={"Authorization": f"Bearer {token}"})
    comparison = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/comparison").json()
    assert comparison["against"] is None and comparison["numbers"]["new"]["accuracy"]["rate"] == 0.0
    assert "same decision 0% of the time" in comparison["reading"]


# ---- US39.13: canary and rollback ---------------------------------------------------------


def _keys_for(session, entry_id, version_id, n) -> list[str]:
    from app.db.models import AgentDeploymentModel

    entry = session.get(AgentDeploymentModel, entry_id)
    return [k for k in (f"case-{i}" for i in range(200)) if route(session, entry, k).id == version_id][:n]


@pytest.mark.usefixtures("systems")
def test_a_canary_takes_its_share_and_rollback_sends_new_cases_back(client, fake_llm):
    process_id, v1, token = _live(client, fake_llm)
    v2 = _new_version(client, process_id)
    canary = _activate(process_id, v2["id"], rollout="canary", share=30)
    assert canary.status_code == 200 and canary.json()["traffic_share"] == 30

    with get_session_factory()() as session, tenant_scope(TEST_TENANT_ID):
        to_canary = _keys_for(session, v1["id"], v2["id"], 3)
        to_stable = _keys_for(session, v1["id"], v1["id"], 3)
        all_keys = [f"case-{i}" for i in range(200)]
        from app.db.models import AgentDeploymentModel

        entry = session.get(AgentDeploymentModel, v1["id"])
        share = sum(route(session, entry, k).id == v2["id"] for k in all_keys) / len(all_keys)
    assert 0.2 < share < 0.4  # about 30%, by key hash
    for key in to_canary + to_stable:
        assert _start(token, v1["id"], {}, key=key).status_code == 202
    # The same key finds the same case, on whichever version took it.
    again = _start(token, v1["id"], {}, key=to_canary[0])
    assert again.status_code == 200
    assert len(_executions(v2["id"])) == 3 and len(_executions(v1["id"])) == 3

    # Rolled back: the canary takes no new case; what it accepted finishes on it.
    blank = client.post(f"/api/processes/{process_id}/deployments/{v2['id']}/rollback", json={"reason": "  "})
    assert blank.status_code == 409 and "say why" in blank.text
    rolled = client.post(f"/api/processes/{process_id}/deployments/{v2['id']}/rollback",
                         json={"reason": "Escalates too often"})
    assert rolled.status_code == 200 and rolled.json()["status"] == "retired"
    assert rolled.json()["stop_reason"] == "Rolled back: Escalates too often"
    assert _start(token, v1["id"], {}, key="after-rollback").json()["id"] in {e["id"] for e in _executions(v1["id"])}
    _reader_llm(fake_llm, process_id, v1["id"])
    _run_jobs()
    assert {e["status"] for e in _executions(v2["id"])} == {"COMPLETED"}
    # A rollback of something that takes every case is refused.
    assert client.post(f"/api/processes/{process_id}/deployments/{v1['id']}/rollback",
                       json={"reason": "x"}).status_code == 409


def test_a_canary_needs_a_live_version(client, fake_llm):
    from .test_runtime import _approved_agent

    process_id, artifact = _approved_agent(client, fake_llm)
    deployment = _deploy(client, process_id, artifact["id"])
    _bind(client, process_id, deployment)
    refused = _activate(process_id, deployment["id"], rollout="canary", share=10)
    assert refused.status_code == 409 and "nothing runs yet" in refused.text


# ---- US39.14: metrics, alerts, traces -----------------------------------------------------


@pytest.mark.usefixtures("systems")
def test_metrics_count_cases_tools_and_failures(client, fake_llm):
    process_id, deployment, token = _live(client, fake_llm)
    _reader_llm(fake_llm, process_id, deployment["id"], fail_on={"FAIL-ME"})
    _start(token, deployment["id"], {"note": "ok"}, key="k1")
    _start(token, deployment["id"], {"note": "FAIL-ME"}, key="k2")
    _run_jobs()
    m = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/metrics?window=hour").json()
    assert m["executions"] == 2 and m["by_status"] == {"COMPLETED": 1, "FAILED": 1}
    assert m["error_rate"]["passed"] == 1 and m["error_rate"]["total"] == 2
    assert m["tools"]["Core banking"]["calls"] == 1 and m["tool_errors"] == 0
    assert m["latency_seconds"]["p50"] is not None and m["tokens"] > 0
    rows = client.get("/api/runtime/deployments?window=week").json()
    row = next(r for r in rows if r["deployment"]["id"] == deployment["id"])
    assert row["deployment"]["process_name"] and row["metrics"]["executions"] == 2
    assert client.get("/api/runtime/deployments?window=year").status_code == 422


@pytest.mark.usefixtures("systems")
def test_an_alert_notifies_calls_its_webhook_and_can_pause(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    url = f"/api/processes/{process_id}/deployments/{deployment['id']}/monitoring"
    for bad, why in (({"metric": "mood", "threshold": 1}, "metric must be"),
                     ({"metric": "error_rate", "threshold": 0.1, "email": "ops@bank.example"}, "email delivery"),
                     ({"metric": "error_rate", "threshold": 0.1, "webhook": "http://hooks.ops.example/a"}, "https")):
        refused = client.put(url, json={"alert_rules": [bad]})
        assert refused.status_code == 422 and why in refused.text, refused.text
    saved = client.put(url, json={"alert_rules": [{"metric": "error_rate", "window": "hour", "op": ">",
                                                   "threshold": 0.4, "webhook": HOOK, "action": "pause"}]})
    assert saved.status_code == 200 and saved.json()["alert_rules"][0]["id"].startswith("alert")

    _reader_llm(fake_llm, process_id, deployment["id"], fail_on={"FAIL-ME"})
    _start(token, deployment["id"], {"note": "FAIL-ME"})
    _run_jobs()
    assert _deployment(client, process_id, deployment["id"])["status"] == "paused"
    with get_owner_engine().connect() as conn:
        note = conn.scalar(text("SELECT message FROM runtime_notifications WHERE kind = 'alert'"))
    assert "error_rate is 1 over the last hour" in note
    assert _run(["runtime_alert"]) == 1
    hook = next(r for r in systems.requests if r.url.host == "hooks.ops.example")
    body = json.loads(hook.content)
    assert body["type"] == "axyntro.runtime.alert" and body["deployment_id"] == deployment["id"]
    assert body["link"].endswith(f"/runtime?deployment={deployment['id']}")


@pytest.mark.usefixtures("systems")
def test_traces_go_to_the_collector_without_payloads(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    url = f"/api/processes/{process_id}/deployments/{deployment['id']}/monitoring"
    assert client.put(url, json={"telemetry": {"otlp_endpoint": COLLECTOR,
                                               "headers": {"x-api-key": "plain"}}}).status_code == 422
    assert client.put(url, json={"telemetry": {"otlp_endpoint": COLLECTOR,
                                               "headers": {"x-api-key": "secret://tenant/core_key"}}}).status_code == 200
    _reader_llm(fake_llm, process_id, deployment["id"])
    execution_id = _start(token, deployment["id"], {"customerName": "Ada Lovelace"}).json()["id"]
    _run_jobs()
    assert _run(["runtime_telemetry"]) == 1
    sent = {r.url.path: r for r in systems.requests if r.url.host == "otel.ops.example"}
    traces = json.loads(sent["/v1/traces"].content)
    assert sent["/v1/traces"].headers["x-api-key"]  # resolved from the secret reference
    spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    with get_owner_engine().connect() as conn:
        steps = conn.scalar(text("SELECT count(*) FROM execution_steps WHERE execution_id = :e"), {"e": execution_id})
    assert len(spans) == steps + 1 and spans[0]["name"] == "agent.execution"
    assert all(s["traceId"] == spans[0]["traceId"] for s in spans)
    resource = {a["key"]: a["value"] for a in traces["resourceSpans"][0]["resource"]["attributes"]}
    assert resource["axyntro.deployment"]["stringValue"] == deployment["id"]
    raw = sent["/v1/traces"].content.decode()
    assert "Ada" not in raw and "low risk" not in raw  # no inputs, outputs or tool responses
    names = [m["name"] for m in json.loads(sent["/v1/metrics"].content)["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
    assert "axyntro.runtime.executions" in names and "axyntro.runtime.duration" in names


# ---- US39.15: drift ----------------------------------------------------------------------


def _baseline() -> dict:
    return {
        "available": True, "batch_id": "batch_x", "decision_field": "decision", "runs": 40,
        "pass_rate": {"passed": 38, "total": 40, "rate": 0.95, "low": 0.83, "high": 0.99},
        "decision_mix": {"approve": 0.7, "reject": 0.3},
        "escalation_rate": {"passed": 0, "total": 40, "rate": 0.0, "low": 0.0, "high": 0.09},
        "tool_mix": {"Core banking": 1.0},
        "cost_usd": {"p05": None, "p50": None, "p95": None},
        "latency_seconds": {"p05": 0.0, "p50": 1.0, "p95": 600.0},
        "inputs": {"segment": {"kind": "category", "values": ["retail"], "shares": {"retail": 1.0}}},
    }


@pytest.mark.usefixtures("systems")
def test_drift_flags_a_changed_agent_and_changed_cases_and_can_pause(client, fake_llm):
    process_id, deployment, token = _live(client, fake_llm)
    url = f"/api/processes/{process_id}/deployments/{deployment['id']}"
    assert client.get(f"{url}/drift").json()["status"] == "no_baseline"
    with get_owner_engine().begin() as conn:
        snapshot = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment["id"]})
        snapshot["baseline"] = _baseline()
        conn.execute(text("UPDATE agent_deployments SET snapshot = CAST(:s AS json) WHERE id = :i"),
                     {"s": json.dumps(snapshot), "i": deployment["id"]})
    assert client.put(f"{url}/monitoring", json={"drift": {"min_executions": 3, "action": "pause"}}).status_code == 200
    _reader_llm(fake_llm, process_id, deployment["id"])
    for i in range(2):
        _start(token, deployment["id"], {"segment": "corporate"}, key=f"d{i}")
    _run_jobs()
    assert client.get(f"{url}/drift").json()["status"] == "not_enough"
    _start(token, deployment["id"], {"segment": "corporate"}, key="d2")
    _run_jobs()

    check = client.get(f"{url}/drift").json()
    assert check["status"] == "drift", check
    flagged = {f["metric"] for f in check["behaviour"] if f["flagged"]}
    assert "decision_mix" in flagged and "tool_mix" not in flagged
    assert [f["metric"] for f in check["inputs"] if f["flagged"]] == ["input:segment"]
    assert "The cases changed too" in check["reading"]
    assert _deployment(client, process_id, deployment["id"])["status"] == "paused"
    with get_owner_engine().connect() as conn:
        assert conn.scalar(text("SELECT count(DISTINCT message) FROM runtime_notifications WHERE kind = 'drift'")) == 1

    dataset = client.post(f"/api/processes/{process_id}/datasets", json={"name": "Production cases"}).json()
    added = client.post(f"{url}/drift/to-dataset", json={"dataset_id": dataset["id"]}).json()
    assert added["added"] == 3
    assert client.post(f"{url}/drift/to-dataset", json={"dataset_id": dataset["id"]}).json()["added"] == 0


def test_psi_and_input_profiles():
    assert drift.psi({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == 0
    assert drift.psi({"a": 0.9, "b": 0.1}, {"a": 0.1, "b": 0.9}) > 1
    numbers = drift.profile_field(list(range(100)))
    assert numbers["kind"] == "number" and len(numbers["edges"]) == 9
    assert drift.psi(numbers["shares"], drift.live_shares(numbers, list(range(100)))) == 0
    assert drift.psi(numbers["shares"], drift.live_shares(numbers, [500] * 50)) > 1
    words = drift.profile_field(["retail"] * 8 + ["sme"] * 2)
    assert words["shares"] == {"retail": 0.8, "sme": 0.2}
    assert drift.live_shares(words, ["retail", "corporate", None]) == {"retail": 0.3333, "(other)": 0.3333,
                                                                       "(missing)": 0.3333}


def test_the_baseline_is_frozen_from_the_versions_batch(client, fake_llm):
    from .test_batches import ROWS, _setup
    from .test_results import _flaky_agent, _run_batch

    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    fake_llm.complete.side_effect = _flaky_agent({})
    _run_batch(client, process, scenario)
    with get_session_factory()() as session, tenant_scope(TEST_TENANT_ID):
        base = drift.baseline(session, artifact["id"], artifact["version"], ["record_id"])
        none = drift.baseline(session, artifact["id"], artifact["version"] + 1, [])
    assert base["available"] and base["runs"] == len(ROWS) and base["dataset_id"] == dataset["id"]
    assert set(base["decision_mix"]) <= {"approve", "reject", "escalate", "none", "error"}
    assert base["tool_mix"] and base["pass_rate"]["total"] == len(ROWS)
    assert base["inputs"]["record_id"]["kind"] == "category"
    assert none == {"available": False, "reason": none["reason"]} and "without a finished correctness batch" in none["reason"]
