"""Epic 34: batch execution at volume."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.batches import service as batches
from app.config import get_settings
from app.db.models import BatchRunModel, JobModel, SimulationRunModel
from app.db.session import get_session_factory
from app.jobs.worker import run_pending
from app.llm.client import LLMClient
from app.llm.types import ToolCall
from tests.test_api_twin import _llm_result, _make_artifact, _scenario_payload

SCHEMA = {
    "tools": [
        {
            "system_name": "CRM API",
            "tool_name": "lookup_record",
            "description": "Look up a record.",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}}},
            "response_shape_description": "An object with a 'tier' field.",
        }
    ]
}


def _agent(messages, **kwargs):
    """Plays the agent and the schema inference: looks a record up, then
    approves records starting with "a" and rejects the rest."""
    if kwargs.get("operation") == "twin_tool_schema_inference":
        return _llm_result(text=json.dumps(SCHEMA))
    if not any(m.role == "tool" for m in messages):
        return _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_record", arguments={"record_id": "x"})])
    user_text = next((m.content for m in messages if m.role == "user" and isinstance(m.content, str)), "")
    decision = "approve" if '"record_id": "a' in user_text else "reject"
    return _llm_result(text=json.dumps({"summary": "done", "decision": decision}))


def _setup(client, fake_llm, rows):
    process, artifact = _make_artifact(client)
    fake_llm.complete.side_effect = _agent
    dataset = client.post(f"/api/processes/{process['id']}/datasets", json={"name": "G", "agent_artifact_id": artifact["id"]}).json()
    client.post(f"/api/processes/{process['id']}/datasets/{dataset['id']}/rows", json=rows)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios",
        json=_scenario_payload(name="Volume", dataset_id=dataset["id"], step_match="in_order", expected_steps=[{"kind": "tool_call", "target": "CRM API"}], expected_outputs={}),
    ).json()
    return process, artifact, dataset, scenario


ROWS = [
    {"row_key": "a1", "inputs": {"record_id": "a1"}, "labels": {"decision": "approve"}, "segment": "typical"},
    {"row_key": "a2", "inputs": {"record_id": "a2"}, "labels": {"decision": "approve"}, "segment": "typical"},
    {"row_key": "b1", "inputs": {"record_id": "b1"}, "labels": {"decision": "reject"}, "segment": "edge"},
    {"row_key": "b2", "inputs": {"record_id": "b2"}, "labels": {"decision": "approve"}, "segment": "edge", "critical": True},
]


def _batch_url(process_id, path=""):
    return f"/api/processes/{process_id}{path}"


def test_select_rows_is_stratified_and_keeps_critical():
    rows = [SimpleNamespace(row_key=f"t{i}", segment="typical", critical=False) for i in range(80)]
    rows += [SimpleNamespace(row_key=f"e{i}", segment="edge", critical=False) for i in range(20)]
    rows += [SimpleNamespace(row_key="crit", segment="edge", critical=True)]
    chosen = batches.select_rows(rows, tier="smoke", size=None, fraction=None, seed=1)
    assert len(chosen) == 10 and chosen[0].row_key == "crit"
    assert sum(1 for r in chosen if r.segment == "edge") >= 2  # the small segment is represented
    assert [r.row_key for r in chosen] == [r.row_key for r in batches.select_rows(rows, tier="smoke", size=None, fraction=None, seed=1)]
    assert len(batches.select_rows(rows, tier="full", size=None, fraction=None, seed=1)) == 101
    assert len(batches.select_rows(rows, tier="custom", size=None, fraction=0.5, seed=1)) == 50


def test_batch_runs_rows_times_trials_and_completes(client, fake_llm):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    estimate = client.post(_batch_url(process["id"], f"/scenarios/{scenario['id']}/batches/estimate"), json={"tier": "full", "trials": 2}).json()
    assert estimate["estimate"]["runs"] == 8 and "default guess" in estimate["estimate"]["basis"]

    started = client.post(_batch_url(process["id"], f"/scenarios/{scenario['id']}/batches"), json={"tier": "full", "trials": 2})
    assert started.status_code == 202, started.text
    batch = client.get(_batch_url(process["id"], f"/batches/{started.json()['id']}")).json()
    s = get_session_factory()()
    errors = [j.last_error for j in s.query(JobModel).all() if j.last_error]
    s.close()
    assert batch["status"] == "completed", errors[:2]
    assert batch["progress"] == {"total": 8, "finished": 8, "passed": 6, "failed": 2, "errored": 0, "cancelled": 0}

    session = get_session_factory()()
    try:
        runs = session.query(SimulationRunModel).filter(SimulationRunModel.batch_id == batch["id"]).all()
        assert {(r.row_key, r.trial) for r in runs} == {(k, t) for k in ("a1", "a2", "b1", "b2") for t in (1, 2)}
        assert len({r.seed for r in runs}) == 8  # each row x trial its own seed
        failed = [r for r in runs if r.graded_passed is False]
        assert {r.row_key for r in failed} == {"b2"} and all(r.steps for r in failed)  # failures keep traces
        assert sum(1 for r in runs if r.graded_passed and not r.steps) >= 4  # most passing traces pruned
        failed_id = failed[0].id
        failed_seed = failed[0].seed
    finally:
        session.close()

    replay = client.post(_batch_url(process["id"], f"/batches/{batch['id']}/runs/{failed_id}/replay"))
    assert replay.status_code == 202
    session = get_session_factory()()
    try:
        again = session.get(SimulationRunModel, replay.json()["run_id"])
        assert again.seed == failed_seed and again.row_key == "b2" and again.batch_id is None
    finally:
        session.close()


def test_pause_resume_and_budget(client, fake_llm, monkeypatch):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    monkeypatch.setattr(get_settings(), "job_worker_enabled", True)  # queue only; we drive it
    batch = client.post(_batch_url(process["id"], f"/scenarios/{scenario['id']}/batches"), json={"tier": "full"}).json()
    client.post(_batch_url(process["id"], f"/batches/{batch['id']}/control"), json={"action": "pause"})
    asyncio.run(run_pending(kinds=["twin_run"]))
    view = client.get(_batch_url(process["id"], f"/batches/{batch['id']}")).json()
    assert view["status"] == "paused" and view["progress"]["finished"] == 0  # nothing ran

    client.post(_batch_url(process["id"], f"/batches/{batch['id']}/control"), json={"action": "resume"})
    session = get_session_factory()()
    try:
        for job in session.query(JobModel).filter(JobModel.status == "queued").all():
            job.run_after = job.run_after.replace(year=2000)  # skip the wait
        session.commit()
    finally:
        session.close()
    # The budget runs out after the first run.
    calls = {"n": 0}

    def fake_spent(session, batch_id):
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 999.0

    monkeypatch.setattr(batches, "spent", fake_spent)
    session = get_session_factory()()
    try:
        session.get(BatchRunModel, batch["id"]).budget_usd = 1.0
        session.commit()
    finally:
        session.close()
    asyncio.run(run_pending(kinds=["twin_run"]))
    view = client.get(_batch_url(process["id"], f"/batches/{batch['id']}")).json()
    assert view["status"] == "stopped_budget"
    assert view["progress"]["cancelled"] == 3 and view["progress"]["finished"] == 4


def test_cancel_marks_pending_runs(client, fake_llm, monkeypatch):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    monkeypatch.setattr(get_settings(), "job_worker_enabled", True)
    batch = client.post(_batch_url(process["id"], f"/scenarios/{scenario['id']}/batches"), json={"tier": "full"}).json()
    view = client.post(_batch_url(process["id"], f"/batches/{batch['id']}/control"), json={"action": "cancel"}).json()
    assert view["status"] == "cancelled" and view["progress"]["cancelled"] == 4
    assert client.post(_batch_url(process["id"], f"/batches/{batch['id']}/control"), json={"action": "resume"}).status_code == 400


def test_schedule_runs_when_dataset_changes(client, fake_llm, monkeypatch):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    monkeypatch.setattr(get_settings(), "job_worker_enabled", True)
    schedule = client.post(_batch_url(process["id"], "/batch-schedules"), json={"scenario_id": scenario["id"], "tier": "smoke"}).json()
    session = get_session_factory()()
    try:
        assert batches.enqueue_due_batches(session) == 0  # nothing changed yet
        session.commit()
    finally:
        session.close()
    client.post(f"/api/processes/{process['id']}/datasets/{dataset['id']}/rows", json=[{"row_key": "a3", "inputs": {"record_id": "a3"}, "labels": {"decision": "approve"}}])
    session = get_session_factory()()
    try:
        assert batches.enqueue_due_batches(session) == 1
        session.commit()
        created = session.query(BatchRunModel).filter(BatchRunModel.schedule_id == schedule["id"]).one()
        assert created.estimate["trigger"] == "dataset changed" and created.dataset_version == 3
        assert batches.enqueue_due_batches(session) == 0  # not again while that batch is running
    finally:
        session.close()


def test_load_batch_runs_concurrently(client, fake_llm):
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    started = client.post(
        _batch_url(process["id"], f"/scenarios/{scenario['id']}/batches"),
        json={"tier": "full", "mode": "load", "target_concurrency": 4, "stub_latency_ms": 20},
    ).json()
    view = client.get(_batch_url(process["id"], f"/batches/{started['id']}")).json()
    assert view["mode"] == "load" and view["status"] == "completed" and view["progress"]["finished"] == 4
    assert view["load_settings"] == {"target_concurrency": 4, "stub_latency_ms": 20}


def test_llm_client_marks_cache_and_bounds_concurrency(monkeypatch):
    client = LLMClient()
    payload = {"model": "anthropic/claude-sonnet-5", "messages": [{"role": "system", "content": "stable"}, {"role": "user", "content": "x"}]}
    client._mark_cacheable(payload)
    assert payload["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    plain = {"model": "qwen/qwen3.7-flash", "messages": [{"role": "system", "content": "stable"}]}
    client._mark_cacheable(plain)
    assert plain["messages"][0]["content"] == "stable"
    monkeypatch.setattr(client._settings, "llm_max_concurrency", 2)
    slot = client._slot("https://x", "m")
    assert slot is client._slot("https://x", "m") and slot._value == 2
