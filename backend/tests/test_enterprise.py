"""Epic 31: job queue, connectors, federation, model policy, audit."""

import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors import jobs as connector_jobs
from app.connectors import mcp as mcp_module
from app.connectors import sources
from app.connectors.base import ConnectorError
from app.connectors.sources import parse_oscal_catalog
from app.db.models import ConnectorModel, JobModel
from app.db.session import get_session_factory
from app.jobs import queue
from app.jobs.worker import handler, run_pending
from app.llm.client import LLMClient
from app.llm.exceptions import LLMPolicyError
from app.llm.routing import LlmPolicy, LlmRoute, current_route, redact, route_from_policy
from app.llm.types import ChatMessage
from app.main import app
from app.llm.client import get_llm_client
from tests.test_api_blueprint import _finalize, _llm_json, _node_result

CALLS: list[dict] = []


@handler("test_echo")
async def _echo(payload, session):
    CALLS.append(payload)
    if payload.get("fail"):
        raise RuntimeError("boom")
    return {"echo": payload.get("value")}


def _session():
    return get_session_factory()()


# ---- US31.1 job queue -----------------------------------------------------


def test_queue_runs_retries_and_dedupes(client):
    CALLS.clear()
    s = _session()
    try:
        ok = queue.enqueue(s, "test_echo", {"value": 1})
        again = queue.enqueue(s, "test_echo", {"value": 2}, dedupe_key="only-one")
        same = queue.enqueue(s, "test_echo", {"value": 3}, dedupe_key="only-one")
        failing = queue.enqueue(s, "test_echo", {"fail": True}, max_attempts=2)
        s.commit()
        assert same.id == again.id
        ids = (ok.id, again.id, failing.id)
    finally:
        s.close()

    assert asyncio.run(run_pending(kinds=["test_echo"])) == 3
    s = _session()
    try:
        assert s.get(JobModel, ids[0]).status == "succeeded" and s.get(JobModel, ids[0]).result == {"echo": 1}
        retry = s.get(JobModel, ids[2])
        assert retry.status == "queued" and retry.attempts == 1 and "boom" in retry.last_error
        retry.run_after = retry.run_after - timedelta(hours=1)  # skip the backoff
        s.commit()
    finally:
        s.close()
    asyncio.run(run_pending(kinds=["test_echo"]))
    s = _session()
    try:
        assert s.get(JobModel, ids[2]).status == "failed" and s.get(JobModel, ids[2]).attempts == 2
    finally:
        s.close()


def test_expired_lease_is_recovered(client):
    s = _session()
    try:
        job = queue.enqueue(s, "test_echo", {"value": 9})
        s.commit()
        [claimed] = queue.claim(s, "dead-worker", kinds=["test_echo"], limit=1)
        claimed.locked_at = claimed.locked_at - timedelta(hours=2)
        s.commit()
        assert queue.recover_expired(s, lease_seconds=600) == 1
        s.commit()
        assert s.get(JobModel, job.id).status == "queued"
    finally:
        s.close()


def test_scheduler_queues_due_syncs_once(client):
    s = _session()
    try:
        s.add(ConnectorModel(id="conn_a", name="cmdb", type="http", config={}, schedule_minutes=60))
        s.add(ConnectorModel(id="conn_b", name="manual", type="http", config={}, schedule_minutes=None))
        s.commit()
        assert connector_jobs.enqueue_due_syncs(s) == 1
        connector_jobs.enqueue_due_syncs(s)
        s.commit()
        assert len([j for j in s.query(JobModel).all() if j.kind == "connector_sync"]) == 1
    finally:
        s.close()


# ---- US31.2-31.6 connectors -----------------------------------------------


def _mock_http(monkeypatch, module, handler_fn):
    real = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler_fn), **kw))


def _connector(client, **body):
    response = client.post("/api/organization/connectors", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_http_connector_creates_updates_retires_with_impact(client, monkeypatch):
    feed = {"result": [{"sys_id": "1", "name": "Core Banking", "aliases": "CBP;core", "vendor": {"name": "Temenos"}},
                       {"sys_id": "2", "name": "CRM", "vendor": {"name": "Salesforce"}}]}
    _mock_http(monkeypatch, sources, lambda request: httpx.Response(200, json=feed))
    connector = _connector(
        client,
        name="servicenow-cmdb",
        type="http",
        config={
            "url": "https://cmdb.example/api/apps",
            "kind": "application",
            "records_key": "result",
            "mapping": {"external_id": "sys_id", "name": "name", "data": {"aliases": "aliases", "vendor": "vendor.name"}},
        },
    )
    first = client.post(f"/api/organization/connectors/{connector['id']}/sync").json()
    assert first["queued"] is False and first["run"]["stats"]["created"] == 2
    apps = {a["name"]: a for a in client.get("/api/organization/entities?kind=application").json()}
    assert apps["Core Banking"]["data"]["aliases"] == ["CBP", "core"]
    assert apps["Core Banking"]["source"] == "connector:servicenow-cmdb"

    feed["result"] = [{"sys_id": "1", "name": "Core Banking", "aliases": "CBP;core", "vendor": {"name": "Temenos SA"}}]
    second = client.post(f"/api/organization/connectors/{connector['id']}/sync").json()["run"]
    assert second["stats"]["updated"] == 1 and second["stats"]["retired"] == 1
    assert {i["entity_name"] for i in second["impacts"]} == {"Core Banking", "CRM"}
    assert "0 blueprints, 0 agents, 0 regression suites affected" in second["impacts"][0]["summary"]
    assert client.get("/api/organization/entities?kind=application").json()[0]["version"] == 2

    runs = client.get(f"/api/organization/connectors/{connector['id']}/runs").json()
    assert len(runs) == 2


def test_connector_config_refuses_secrets(client):
    bad = client.post(
        "/api/organization/connectors",
        json={"name": "x", "type": "http", "config": {"url": "https://x", "api_key": "sk-123"}},
    )
    assert bad.status_code == 400 and "token_env" in bad.json()["detail"]
    assert client.post("/api/organization/connectors", json={"name": "y", "type": "nope"}).status_code == 400


def test_folder_connector_ingests_changed_policies(client, fake_llm, monkeypatch, tmp_path):
    monkeypatch.setattr(connector_jobs, "get_job_llm", lambda: fake_llm)
    fake_llm.complete.return_value = _llm_json(
        {"controls": [{"title": "MLRO reviews matches", "requires_human_review": True, "citations": ["P1"], "confidence": 0.9}]}
    )
    (tmp_path / "aml-policy.txt").write_text("1. Sanctions\nPotential matches must be reviewed by the MLRO.", encoding="utf-8")
    (tmp_path / "ignore.png").write_bytes(b"\x89PNG")
    connector = _connector(client, name="policy-library", type="folder", config={"path": str(tmp_path)})

    run = client.post(f"/api/organization/connectors/{connector['id']}/sync").json()["run"]
    assert run["stats"]["created"] == 1 and run["stats"]["documents_queued"] == 1
    policies = client.get("/api/organization/entities?kind=policy").json()
    assert policies[0]["name"] == "aml policy" and policies[0]["source"] == "connector:policy-library"
    assert [p["name"] for p in client.get("/api/organization/entities?status=proposed").json()] == ["MLRO reviews matches"]

    unchanged = client.post(f"/api/organization/connectors/{connector['id']}/sync").json()["run"]
    assert unchanged["stats"]["unchanged"] == 1 and unchanged["stats"]["documents_queued"] == 0

    (tmp_path / "aml-policy.txt").write_text("1. Sanctions\nPotential matches must be reviewed by the MLRO within a day.", encoding="utf-8")
    changed = client.post(f"/api/organization/connectors/{connector['id']}/sync").json()["run"]
    assert changed["stats"]["updated"] == 1 and changed["stats"]["documents_queued"] == 1
    assert client.get("/api/organization/entities?kind=policy").json()[0]["version"] == 2


def test_oscal_and_scim_parsing(monkeypatch):
    catalog = {
        "catalog": {
            "groups": [
                {"id": "ac", "controls": [
                    {"id": "ac-2", "title": "Account Management", "parts": [{"name": "statement", "prose": "Manage accounts."}],
                     "controls": [{"id": "ac-2.1", "title": "Automated Account Management"}]},
                ]}
            ]
        }
    }
    items = parse_oscal_catalog(catalog)
    assert [(i.code, i.name) for i in items] == [("AC-2", "Account Management"), ("AC-2.1", "Automated Account Management")]
    assert items[0].data["description"] == "Manage accounts."
    with pytest.raises(ConnectorError):
        parse_oscal_catalog({"profile": {}})

    pages = [
        {"totalResults": 3, "Resources": [{"id": "g1", "displayName": "AXY-MLRO", "members": [{"display": "Ada"}]},
                                          {"id": "g2", "displayName": "Other"}]},
        {"totalResults": 3, "Resources": [{"id": "g3", "displayName": "AXY-Head of Operations"}]},
    ]
    seen = []

    def scim(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=pages[len(seen) - 1])

    _mock_http(monkeypatch, sources, scim)
    batch = asyncio.run(sources.ScimConnector({"url": "https://idp.example/scim/v2", "group_prefix": "AXY-"}).fetch())
    assert [(i.name, i.data["members"]) for i in batch.items] == [("MLRO", ["Ada"]), ("Head of Operations", [])]
    assert seen[1]["startIndex"] == "3"


def test_mcp_connector_reads_via_tool_and_refuses_write_tools(monkeypatch):
    methods = []

    def server(request):
        message = json.loads(request.content)
        methods.append(message.get("method"))
        if message.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18"}}, headers={"mcp-session-id": "s1"})
        if message.get("method") == "tools/call":
            assert request.headers["mcp-session-id"] == "s1"
            records = [{"id": "c1", "title": "Four-eyes payment release", "text": "Two people approve."}]
            body = "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": json.dumps(records)}]}}) + "\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(202)

    _mock_http(monkeypatch, mcp_module, server)
    config = {"url": "https://grc.example/mcp", "tool": "list_controls", "kind": "control",
              "mapping": {"external_id": "id", "name": "title", "data": {"description": "text"}}}
    batch = asyncio.run(mcp_module.McpConnector(config).fetch())
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert batch.items[0].name == "Four-eyes payment release" and batch.items[0].data == {"description": "Two people approve."}
    with pytest.raises(ConnectorError, match="read tools"):
        asyncio.run(mcp_module.McpConnector({**config, "tool": "update_control"}).fetch())


# ---- US31.7 federation ----------------------------------------------------


@pytest.fixture
def unit_client(fake_llm):
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as other:
        other.post("/api/auth/login", json={"name": "banking-steward", "roles": ["analyst", "architect", "process_owner"]})
        yield other
    app.dependency_overrides.pop(get_llm_client, None)


def _create(client, **body):
    response = client.post("/api/organization/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_federated_visibility_and_stewardship(client, unit_client):
    banking = _create(client, kind="business_unit", name="Retail Banking")
    insurance = _create(client, kind="business_unit", name="Insurance")
    group_rule = _create(client, kind="control", name="Group infosec rule")
    insurance_rule = _create(client, kind="control", name="Claims rule", owner_business_unit_id=insurance["id"])
    member = next(m for m in client.get("/api/organization/members").json() if m["name"] == "banking-steward")
    client.put(f"/api/organization/members/{member['user_id']}", json=[{"business_unit_id": banking["id"], "steward": True}])

    names = {e["name"] for e in unit_client.get("/api/organization/entities?kind=control").json()}
    assert names == {"Group infosec rule"}  # not Insurance's
    assert unit_client.get(f"/api/organization/entities/{insurance_rule['id']}").status_code == 404
    assert unit_client.put(f"/api/organization/entities/{group_rule['id']}", json={"name": "x"}).status_code == 403

    own = _create(unit_client, kind="control", name="Banking rule")
    assert own["owner_business_unit_id"] == banking["id"]
    assert unit_client.put(f"/api/organization/entities/{own['id']}", json={"name": "Banking rule v2"}).json()["version"] == 2
    assert unit_client.put(f"/api/organization/members/{member['user_id']}", json=[]).status_code == 403
    assert unit_client.get("/api/organization/audit-log").status_code == 403


# ---- US31.8 model policy --------------------------------------------------


def test_route_policy_and_redaction(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_provider_region", "")
    blocked = route_from_policy(LlmPolicy(residency=["EU"]), unit_name="Retail Banking")
    assert "requires models hosted in EU" in blocked.blocked_reason
    assert "does not allow" in route_from_policy(LlmPolicy(allowed_providers=["self_hosted"]), unit_name="X").blocked_reason
    routed = route_from_policy(
        LlmPolicy(residency=["EU"], base_url="http://llm.internal:8000/v1", provider_region="EU", model="local-model", redact_pii=True),
        unit_name="Retail Banking",
    )
    assert routed.blocked_reason is None and routed.model == "local-model"
    text = redact("Mail ada@example.com, IBAN DE89 3704 0044 0532 0130 00, SSN 912-34-5678, call +44 20 7946 0958")
    assert "ada@example.com" not in text and "DE89" not in text and "912-34-5678" not in text and "7946" not in text


def test_llm_client_enforces_route(monkeypatch):
    client = LLMClient()
    create = AsyncMock()
    token = current_route.set(LlmRoute(blocked_reason="nope"))
    try:
        with pytest.raises(LLMPolicyError, match="nope"):
            asyncio.run(client.complete([ChatMessage(role="user", content="hi")], operation="t"))
    finally:
        current_route.reset(token)

    routed = client._client_for(LlmRoute(base_url="http://llm.internal/v1"))
    assert str(routed.base_url).startswith("http://llm.internal/v1") and routed is not client._client
    monkeypatch.setattr(routed.chat.completions, "create", create)
    create.side_effect = RuntimeError("stop")
    token = current_route.set(LlmRoute(base_url="http://llm.internal/v1", model="m", redact_pii=True))
    try:
        with pytest.raises(Exception):
            asyncio.run(client.complete([ChatMessage(role="user", content="email ada@example.com")], operation="t"))
    finally:
        current_route.reset(token)
    sent = create.call_args.kwargs
    assert sent["model"] == "m" and "ada@example.com" not in json.dumps(sent["messages"])


def test_blocked_unit_policy_returns_403(client, fake_llm):
    unit = _create(client, kind="business_unit", name="Retail Banking", data={"llm_policy": {"residency": ["EU"]}})
    process = client.post("/api/processes", json={"name": "P", "scope": {"business_unit_id": unit["id"]}}).json()
    _finalize(client, process["id"])
    real = LLMClient()
    app.dependency_overrides[get_llm_client] = lambda: real
    try:
        response = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    finally:
        app.dependency_overrides[get_llm_client] = lambda: fake_llm
    assert response.status_code == 403 and "requires models hosted in EU" in response.json()["detail"]


# ---- US31.9 / US31.10 changes and audit -----------------------------------


def test_changes_feed_and_audit_log(client, fake_llm):
    control = _create(client, kind="control", code="AML-4", name="Sanctions review", applies_to={"jurisdictions": ["EU"]})
    client.put(f"/api/organization/entities/{control['id']}", json={"name": "Sanctions review (v2)", "change_note": "tightened"})
    changes = client.get("/api/organization/changes").json()
    assert changes[0]["name"] == "AML-4" and changes[0]["version"] == 2 and changes[0]["change_note"] == "tightened"
    assert "affected" in changes[0]["impact"]["summary"]

    process = client.post("/api/processes", json={"name": "P", "scope": {"jurisdictions": ["DE"]}}).json()
    _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(
        {"nodes": [_node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="x"),
                   _node_result("Task_a"),
                   _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="x")]}
    )
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    client.get(f"/api/organization/entities/{control['id']}")

    log = client.get("/api/organization/audit-log").json()
    actions = {(e["actor_type"], e["action"]) for e in log}
    assert ("llm", "llm_context") in actions and ("user", "view_entity") in actions
    llm_read = next(e for e in log if e["action"] == "llm_context")
    assert llm_read["actor"] == "llm:blueprint_evaluation" and control["id"] in llm_read["entity_ids"]
    assert client.get(f"/api/organization/audit-log?entity_id={control['id']}").json()


def test_worker_tick_claims_and_runs_with_concurrency(client):
    from app.jobs.worker import Worker

    CALLS.clear()
    s = _session()
    try:
        for value in range(3):
            queue.enqueue(s, "test_echo", {"value": value})
        s.commit()
    finally:
        s.close()

    async def drive():
        worker = Worker()
        worker.concurrency = 2
        await worker.tick()
        first = len([t for tasks in worker._running.values() for t in tasks])
        await asyncio.gather(*[t for tasks in worker._running.values() for t in tasks])
        await worker.tick()
        await asyncio.gather(*[t for tasks in worker._running.values() for t in tasks])
        return first

    assert asyncio.run(drive()) == 2  # per-kind concurrency limit
    assert sorted(c["value"] for c in CALLS) == [0, 1, 2]
