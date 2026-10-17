"""Epic 39, US39.18: a certified hand-off to the customer's own agent
platform, and its executions reported back."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.runtime import handoff

from .test_runtime import _approved_agent, _client_token, _deploy, _isolate, _start  # noqa: F401
from .test_runtime_rollout import _approver


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_SIGNING_KEY_FILE", str(tmp_path / "handoff.pem"))
    get_settings.cache_clear()
    handoff._key = None
    yield
    handoff._key = None
    monkeypatch.undo()
    get_settings.cache_clear()


def _handed_off(client, fake_llm) -> tuple[str, dict, dict]:
    process_id, artifact = _approved_agent(client, fake_llm, writes=True)
    deployment = _deploy(client, process_id, artifact["id"])
    url = f"/api/processes/{process_id}/deployments/{deployment['id']}/handoff"
    assert client.post(url).status_code == 403  # who asked for it can't hand it off
    handed = _approver().post(url)
    assert handed.status_code == 200, handed.text
    return process_id, deployment, handed.json()


def _keys() -> dict:
    return TestClient(app).get("/runtime/v1/handoff/keys").json()


def test_the_bundle_is_signed_and_carries_card_tools_and_evidence(client, fake_llm):
    process_id, deployment, bundle = _handed_off(client, fake_llm)
    assert bundle["format"] == "axyntro.agent-handoff/v1"
    claims = handoff.verify(bundle, _keys())
    assert claims["sub"] == deployment["id"] and claims["agent"]["version"] == 1
    assert claims["twin_baseline"]["available"] is False  # approved without a batch, and it says so

    card = bundle["agent_card"]
    assert card["skills"][0]["name"] == card["name"] and card["defaultInputModes"] == ["application/json"]
    tool, = bundle["mcp_tools"]
    assert tool["inputSchema"]["type"] == "object"
    assert tool["annotations"]["readOnlyHint"] is False and tool["annotations"]["destructiveHint"] is True
    assert bundle["tool_contracts"][0]["system_name"] == "Core banking"
    assert set(bundle["guardrails"]) == {"runtime", "escalation_policy", "policy_guardrails"}

    # Fetched again, it's the same content under a fresh signature.
    again = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/handoff").json()
    assert handoff.verify(again, _keys())["content_sha256"] == claims["content_sha256"]

    tampered = copy.deepcopy(bundle)
    tampered["guardrails"]["runtime"]["max_cost_usd"] = 1000
    with pytest.raises(ValueError, match="changed after it was signed"):
        handoff.verify(tampered, _keys())


def test_a_handed_off_agent_runs_nowhere_here(client, fake_llm):
    process_id, deployment, _ = _handed_off(client, fake_llm)
    detail = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()
    assert detail["status"] == "external"
    token = _client_token(client)
    refused = _start(token, deployment["id"], {})
    assert refused.status_code == 503 and "your own platform" in refused.text
    assert _approver().post(f"/api/processes/{process_id}/deployments/{deployment['id']}/handoff").status_code == 409


def test_reports_from_the_other_platform_feed_metrics_and_drift(client, fake_llm):
    process_id, deployment, _ = _handed_off(client, fake_llm)
    token = _client_token(client)
    url = f"/runtime/v1/deployments/{deployment['id']}/reports"
    caller = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    runs = [
        {"id": "r1", "status": "COMPLETED", "started_at": "2026-10-17T09:00:00Z", "completed_at": "2026-10-17T09:00:04Z",
         "input": {"customerId": "C-1"}, "final_output": {"accountId": "A-1", "decision": "approve"},
         "total_cost_usd": 0.002, "total_tokens": 900, "tool_calls": [{"system": "Core banking"},
                                                                        {"system": "Core banking", "failed": True}],
         "outcome": {"decision": "approve"}},
        {"id": "r2", "status": "FAILED", "tool_calls": []},
        {"id": "r2", "status": "FAILED"},  # sent twice in one batch
    ]
    sent = caller.post(url, json={"executions": runs})
    assert sent.status_code == 200 and sent.json() == {"accepted": 2, "duplicates": 1}
    assert caller.post(url, json={"executions": runs[:1]}).json() == {"accepted": 0, "duplicates": 1}
    bad = caller.post(url, json={"executions": [{"id": "r3", "status": "RUNNING"}]})
    assert bad.status_code == 409 and "status must be one of" in bad.text
    assert TestClient(app).post(url, json={"executions": []}).status_code == 401

    m = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/metrics?window=week").json()
    assert m["executions"] == 2 and m["by_status"] == {"COMPLETED": 1, "FAILED": 1}
    assert m["tools"]["Core banking"] == {"calls": 2, "errors": 1, "not_sent": 0}
    listed = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/executions").json()
    reported = next(e for e in listed if e["status"] == "COMPLETED")
    assert reported["trigger"] == "external"
    detail = client.get(f"/api/processes/{process_id}/executions/{reported['id']}").json()
    assert [s["type"] for s in detail["steps"]] == ["TOOL_EXECUTED", "TOOL_FAILED"]
    assert detail["reported_outcome"] == {"decision": "approve"}
    assert client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}/drift").json()["status"] == \
        "no_baseline"


def test_only_a_handed_off_deployment_takes_reports(client, fake_llm):
    process_id, artifact = _approved_agent(client, fake_llm)
    deployment = _deploy(client, process_id, artifact["id"])
    token = _client_token(client)
    refused = TestClient(app).post(f"/runtime/v1/deployments/{deployment['id']}/reports",
                                   json={"executions": [{"id": "x", "status": "COMPLETED"}]},
                                   headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 409 and "handed-off" in refused.text
