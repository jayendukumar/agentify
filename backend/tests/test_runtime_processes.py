"""Epic 39, US39.17: a case through the whole process -- its deployed
agents along the real flow, and a person where no agent runs."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.bpmn.builder import build_bpmn_xml
from app.db.session import get_owner_engine
from app.jobs.worker import run_pending
from app.main import app
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef

from .test_runtime import FakeSystems, _client_token, _isolate, _live, _result, _schema_tool, systems  # noqa: F401
from .test_runtime_rollout import _approver


def _with_a_persons_step(process_id: str, deployment_id: str) -> None:
    """The process the agent was generated from, now with a step after it
    that no agent runs: start -> a (the agent) -> b (a person) -> end."""
    ref = SourceRef(document_id="d", location="p1", excerpt="x")
    schema = ProcessSchema(process_name="P", elements=[
        ProcessElement(id="start", type="start_event", label="Start", source_refs=[ref], confidence="high"),
        ProcessElement(id="a", type="task", label="Open the account", source_refs=[ref], confidence="high"),
        ProcessElement(id="b", type="task", label="Sign the contract", source_refs=[ref], confidence="high"),
        ProcessElement(id="end", type="end_event", label="End", source_refs=[ref], confidence="high"),
    ], flows=[ProcessFlow(id="f1", **{"from": "start"}, to="a"), ProcessFlow(id="f2", **{"from": "a"}, to="b"),
              ProcessFlow(id="f3", **{"from": "b"}, to="end")])
    xml, _ = build_bpmn_xml(process_id, schema)
    with get_owner_engine().begin() as conn:
        version_id = conn.scalar(text(
            "SELECT a.source_baseline_version_id FROM agent_artifacts a JOIN agent_deployments d "
            "ON d.agent_artifact_id = a.id WHERE d.id = :d"), {"d": deployment_id})
        conn.execute(text("UPDATE versions SET xml = :x WHERE id = :v"), {"x": xml, "v": version_id})


def _walk() -> int:
    return asyncio.run(run_pending(kinds=["runtime_process", "runtime_execution"]))


def _case(token: str, process_id: str, data: dict, key: str = "case-1"):
    return TestClient(app).post(f"/runtime/v1/processes/{process_id}/executions", json={"input": data},
                                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key})


@pytest.fixture
def process_live(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    _with_a_persons_step(process_id, deployment["id"])
    tool = _schema_tool(process_id, deployment["id"])

    def respond(messages, **kwargs):
        if not any(getattr(m, "role", None) == "tool" for m in messages):
            return _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})])
        return _result(text_='{"summary": "Ada is low risk"}')

    fake_llm.complete.side_effect = respond
    return process_id, deployment, token


def test_a_case_runs_the_agent_then_waits_for_a_person(client, process_live):
    process_id, deployment, token = process_live
    started = _case(token, process_id, {"customerId": "C-1"})
    assert started.status_code == 202, started.text
    case_id = started.json()["id"]
    assert _case(token, process_id, {"customerId": "C-1"}).json()["id"] == case_id  # same key, same case
    _walk()

    case = client.get(f"/api/processes/{process_id}/process-executions/{case_id}").json()
    assert case["status"] == "WAITING_FOR_HUMAN", case
    child, = case["node_executions"]
    assert child["deployment_id"] == deployment["id"] and child["status"] == "COMPLETED"
    assert child["trigger"] == "process"
    task_id, = case["open_tasks"]
    task = next(t for t in client.get("/api/runtime/inbox").json() if t["id"] == task_id)
    assert task["kind"] == "human_task" and task["summary"] == "Sign the contract"
    assert task["process_execution_id"] == case_id and task["execution_id"] is None
    assert task["agent_name"].endswith("(a person's step)") and task["case_input"]["summary"] == "Ada is low risk"

    reviewer = _approver()
    assert reviewer.post(f"/api/processes/{process_id}/review-tasks/{task_id}/decide",
                         json={"decision": "approve"}).status_code == 409
    resolved = reviewer.post(f"/api/processes/{process_id}/review-tasks/{task_id}/resolve",
                             json={"outcome": {"decision": "approve", "contractId": "K-1"}})
    assert resolved.status_code == 200, resolved.text
    _walk()
    done = TestClient(app).get(f"/runtime/v1/process-executions/{case_id}",
                               headers={"Authorization": f"Bearer {token}"}).json()
    assert done["status"] == "COMPLETED", done
    assert done["final_output"]["contractId"] == "K-1" and done["final_output"]["summary"] == "Ada is low risk"
    case = client.get(f"/api/processes/{process_id}/process-executions/{case_id}").json()
    assert case["visited_path"][1:3] == ["Task_a", "Task_b"] and case["open_tasks"] == []


def test_pausing_and_stopping_the_whole_process(client, process_live):
    process_id, deployment, token = process_live
    url = f"/api/processes/{process_id}/runtime"
    assert client.put(url, json={"status": "paused"}).status_code == 422  # a reason is required
    assert client.put(url, json={"status": "paused", "reason": "Month-end freeze"}).json()["status"] == "paused"
    refused = _case(token, process_id, {"customerId": "C-1"})
    assert refused.status_code == 503 and "Month-end freeze" in refused.text
    assert client.put(url, json={"status": "active"}).json()["status"] == "active"

    waiting = _case(token, process_id, {"customerId": "C-1"}, key="w").json()["id"]
    _walk()
    stopped = client.put(url, json={"status": "killed", "reason": "Wrong accounts opened"})
    assert stopped.status_code == 200
    case = client.get(f"/api/processes/{process_id}/process-executions/{waiting}").json()
    assert case["status"] == "KILLED" and "Wrong accounts opened" in case["deviations"][-1]["reason"]
    assert client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()["status"] == "killed"
    assert not [t for t in client.get("/api/runtime/inbox").json() if t.get("process_execution_id") == waiting]
    assert _case(token, process_id, {"customerId": "C-1"}, key="after").status_code == 503


def test_a_paused_process_holds_a_case_before_its_next_node(client, process_live):
    process_id, _, token = process_live
    case_id = _case(token, process_id, {"customerId": "C-1"}).json()["id"]
    client.put(f"/api/processes/{process_id}/runtime", json={"status": "paused", "reason": "Hold"})
    _walk()
    case = client.get(f"/api/processes/{process_id}/process-executions/{case_id}").json()
    assert case["status"] == "QUEUED" and case["node_executions"] == []


def test_who_may_start_a_process_case(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    limited = client.post("/api/runtime/clients", json={"label": "One agent only",
                                                        "deployment_ids": [deployment["id"]]}).json()["token"]
    assert _case(limited, process_id, {}).status_code == 404
    nothing = client.post("/api/processes", json={"name": "Nothing deployed"}).json()["id"]
    assert _case(_client_token(client), nothing, {}).status_code == 409
