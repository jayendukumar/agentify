"""Epic 39, build step 2: deployments, the execution store and its job,
the API trigger, identity, production adapters, credentials, guardrails
failing closed, and the kill switch."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import AgentExecutionModel, ExecutionStepModel
from app.db.session import get_owner_engine, get_session_factory
from app.jobs.worker import run_pending
from app.llm.types import ChatCompletionResult, ToolCall, Usage
from app.main import app
from app.runtime import credentials

from .conftest import ALL_ROLES, TEST_TENANT_ID, make_tenant, sign_in
from .test_api_blueprint import approve_agent_as_other_user
from .test_catalog import SPEC, _blueprint_with_systems, _create

SECRET = "s3cr3t-core-key-value"
PROD = "https://api.bank.example"
TOKEN_URL = "https://login.bank.example/oauth/token"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("TENANT_TEST_CORE_KEY", SECRET)
    credentials._tokens.clear()
    yield
    credentials._tokens.clear()


class FakeSystems:
    """The production systems and the authorization server, as an httpx
    transport. Records every request."""

    def __init__(self, on_request=None):
        self.requests: list[httpx.Request] = []
        self.on_request = on_request

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            form = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": f"tok-{form.get('scope', [''])[0]}", "expires_in": 600})
        if self.on_request is not None:
            self.on_request(request)
        if request.method == "POST":
            return httpx.Response(201, json={"accountId": "ACC-9"})
        return httpx.Response(200, json={"id": "C-1", "name": "Ada", "riskRating": "low"})


@pytest.fixture
def systems(monkeypatch):
    fake = FakeSystems()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(fake.handler), **kw))
    return fake


def _result(*, calls=None, text_=None) -> ChatCompletionResult:
    tool_calls = [ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls or [])]
    return ChatCompletionResult(text=text_, tool_calls=tool_calls, finish_reason="tool_calls" if tool_calls else "stop",
                                usage=Usage(input_tokens=5, output_tokens=5, total_tokens=10), model="test-model")


def _approved_agent(client, fake_llm, *, writes: bool = False) -> tuple[str, dict]:
    application = _create(client, kind="application", name="Core banking")
    client.put(f"/api/organization/applications/{application['id']}/api-spec",
               files={"file": ("spec.json", json.dumps(SPEC).encode(), "application/json")})
    endpoints = client.put(f"/api/organization/applications/{application['id']}/endpoints",
                           json=[{"url": "http://localhost:9000", "environment": "sandbox"},
                                 {"url": PROD, "environment": "production"}])
    assert endpoints.status_code == 200, endpoints.text
    outputs = [{"name": "accountId", "source_or_destination": "Core banking", "format": "string"}] if writes else []
    process = _blueprint_with_systems(client, fake_llm, ["Core banking"], outputs=outputs)
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()
    approve_agent_as_other_user(process["id"], artifact["id"])
    return process["id"], artifact


def _deploy(client, process_id, artifact_id) -> dict:
    created = client.post(f"/api/processes/{process_id}/agent-artifacts/{artifact_id}/deployments")
    assert created.status_code == 201, created.text
    return created.json()


def _bind(client, process_id, deployment, credential=None, operations=None):
    body = {
        "base_url": PROD,
        "operations": operations or deployment["bindings"][0]["tested_operations"],
        "credential": credential or {"kind": "api_key", "header": "X-API-Key", "secret_ref": "secret://tenant/core_key"},
    }
    return client.put(f"/api/processes/{process_id}/deployments/{deployment['id']}/bindings/Core banking", json=body)


def _activate(process_id, deployment_id, waiver=None):
    approver = TestClient(app)
    sign_in(approver, "runtime-approver", ["approver"])
    return approver.post(f"/api/processes/{process_id}/deployments/{deployment_id}/activate",
                         json={"shadow_waiver": waiver})


def _client_token(client) -> str:
    created = client.post("/api/runtime/clients", json={"label": "Loan system"})
    assert created.status_code == 201, created.text
    return created.json()["token"]


def _start(token, deployment_id, data, key="case-1", host=None):
    caller = TestClient(app, base_url=f"http://{host}") if host else TestClient(app)
    return caller.post(f"/runtime/v1/deployments/{deployment_id}/executions", json={"input": data},
                       headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key})


def _run_jobs() -> int:
    return asyncio.run(run_pending(kinds=["runtime_execution"]))


def _live(client, fake_llm, **kw) -> tuple[str, dict, str]:
    process_id, artifact = _approved_agent(client, fake_llm, **kw)
    deployment = _deploy(client, process_id, artifact["id"])
    assert _bind(client, process_id, deployment).status_code == 200
    activated = _activate(process_id, deployment["id"], waiver="Pilot with a reconciliation check" if kw else None)
    assert activated.status_code == 200, activated.text
    return process_id, activated.json(), _client_token(client)


def _schema_tool(process_id, deployment_id) -> str:
    with get_owner_engine().connect() as conn:
        snapshot = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment_id})
    return next(iter(snapshot["tool_schemas"].values()))["tool_name"]


# ---- deploying ---------------------------------------------------------------------------


def test_deploy_bind_activate_and_run_a_case(client, fake_llm, systems):
    process_id, artifact = _approved_agent(client, fake_llm)
    deployment = _deploy(client, process_id, artifact["id"])
    assert deployment["status"] == "pending"
    assert deployment["identity"]["status"] == "active"
    assert deployment["bindings"][0]["base_url"] == PROD
    assert any("no credential is set" in p for p in deployment["readiness"])

    assert _bind(client, process_id, deployment).status_code == 200
    # Segregation of duties: whoever asked for it (and built it) can't approve it.
    own = client.post(f"/api/processes/{process_id}/deployments/{deployment['id']}/activate", json={})
    assert own.status_code == 403
    activated = _activate(process_id, deployment["id"])
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"

    tool = _schema_tool(process_id, deployment["id"])
    fake_llm.complete.side_effect = [
        _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})]),
        _result(text_='{"summary": "Ada is low risk"}'),
    ]
    token = _client_token(client)
    started = _start(token, deployment["id"], {})
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "QUEUED"
    assert _run_jobs() == 1

    done = TestClient(app).get(f"/runtime/v1/executions/{started.json()['id']}",
                               headers={"Authorization": f"Bearer {token}"}).json()
    assert done["status"] == "COMPLETED", done
    assert done["final_output"] == {"summary": "Ada is low risk"}
    call = next(r for r in systems.requests if r.url.host == "api.bank.example")
    assert str(call.url) == f"{PROD}/customers/C-1" and call.headers["X-API-Key"] == SECRET

    detail = client.get(f"/api/processes/{process_id}/executions/{started.json()['id']}").json()
    types = [s["type"] for s in detail["steps"]]
    assert types[:4] == ["RUN_STARTED", "MODEL_INVOKED", "POLICY_CHECK", "TOOL_EXECUTED"]
    assert types[-1] == "RUN_COMPLETED" and detail["identity_id"] == deployment["identity"]["id"]
    # The model saw the twin's instructions, with the live header.
    run_call = next(c for c in fake_llm.complete.call_args_list if c.kwargs.get("operation") == "runtime_execution")
    instructions = run_call.args[0][1].content
    assert instructions.startswith("Run instructions for this case (not part of your normal operating")

    # Idempotency: the same key is the same execution; with other input, refused.
    again = _start(token, deployment["id"], {})
    assert again.status_code == 200 and again.json()["id"] == started.json()["id"]
    assert _start(token, deployment["id"], {"x": 1}).status_code == 409


def test_regenerating_never_changes_a_running_deployment(client, fake_llm, systems):
    process_id, deployment, _ = _live(client, fake_llm)
    with get_owner_engine().connect() as conn:
        before = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment["id"]})
    client.post(f"/api/processes/{process_id}/blueprint/nodes/Task_a/agent-artifact")
    with get_owner_engine().connect() as conn:
        after = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment["id"]})
    assert before == after
    assert client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()["status"] == "active"


# ---- failing closed --------------------------------------------------------------------


def test_deploying_refuses_what_wasnt_approved_or_tested(client, fake_llm):
    application = _create(client, kind="application", name="Core banking")
    del application
    process = _blueprint_with_systems(client, fake_llm, ["Core banking"])
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()
    refused = client.post(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/deployments")
    assert refused.status_code == 409
    problems = refused.json()["detail"]["problems"]
    assert any("approve this agent version" in p for p in problems)
    assert any("tested with a simulated tool" in p for p in problems)


def test_bindings_accept_only_tested_operations_on_production_endpoints(client, fake_llm):
    process_id, artifact = _approved_agent(client, fake_llm)
    deployment = _deploy(client, process_id, artifact["id"])
    untested = _bind(client, process_id, deployment, operations=["deleteEverything"])
    assert untested.status_code == 409 and "not tested" in untested.text
    sandbox = client.put(f"/api/processes/{process_id}/deployments/{deployment['id']}/bindings/Core banking",
                         json={"base_url": "http://localhost:9000", "operations": ["getCustomer"],
                               "credential": {"kind": "api_key", "secret_ref": "secret://tenant/core_key"}})
    assert sandbox.status_code == 409 and "isn't a production endpoint" in sandbox.text
    inline = _bind(client, process_id, deployment, credential={"kind": "api_key", "secret_ref": SECRET})
    assert inline.status_code == 409 and "not the secret itself" in inline.text


def test_a_changing_agent_needs_shadow_or_a_waiver(client, fake_llm):
    process_id, artifact = _approved_agent(client, fake_llm, writes=True)
    deployment = _deploy(client, process_id, artifact["id"])
    _bind(client, process_id, deployment)
    refused = _activate(process_id, deployment["id"])
    assert refused.status_code == 409 and "runs in shadow first" in refused.text
    assert _activate(process_id, deployment["id"], waiver="Low volume pilot, reconciled daily").status_code == 200


def test_an_unset_secret_blocks_activation(client, fake_llm, monkeypatch):
    monkeypatch.delenv("TENANT_TEST_CORE_KEY")
    process_id, artifact = _approved_agent(client, fake_llm)
    deployment = _deploy(client, process_id, artifact["id"])
    _bind(client, process_id, deployment)
    refused = _activate(process_id, deployment["id"])
    assert refused.status_code == 409 and "the secret isn't set" in refused.text
    assert "TENANT_TEST_CORE_KEY" in refused.text


def test_input_is_checked_before_any_model_call(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    with get_owner_engine().begin() as conn:
        snapshot = conn.scalar(text("SELECT snapshot FROM agent_deployments WHERE id = :i"), {"i": deployment["id"]})
        snapshot["definition"]["input_schema"] = [{"name": "customerId", "source_or_destination": "", "format": "string"}]
        conn.execute(text("UPDATE agent_deployments SET snapshot = CAST(:s AS json) WHERE id = :i"),
                     {"s": json.dumps(snapshot), "i": deployment["id"]})
    refused = _start(token, deployment["id"], {"other": 1})
    assert refused.status_code == 422 and "customerId" in refused.text
    assert fake_llm.complete.await_count == 0 or all(
        "runtime_execution" not in str(c) for c in fake_llm.complete.call_args_list)


def test_a_bad_token_gets_401(client, fake_llm):
    process_id, deployment, token = _live(client, fake_llm)
    del process_id, token
    assert _start("axr_nope", deployment["id"], {}).status_code == 401


# ---- credentials ----------------------------------------------------------------------


def test_oauth_tokens_are_scoped_cached_and_never_stored(client, fake_llm, systems):
    process_id, artifact = _approved_agent(client, fake_llm)
    deployment = _deploy(client, process_id, artifact["id"])
    oauth = {"kind": "oauth_client_credentials", "token_url": TOKEN_URL, "client_id": "agent-loans",
             "secret_ref": "secret://tenant/core_key", "scopes": {"read": ["core.read"], "update": ["core.write"]}}
    assert _bind(client, process_id, deployment, credential=oauth).status_code == 200
    tested = client.post(f"/api/processes/{process_id}/deployments/{deployment['id']}/bindings/Core banking/test-credential")
    assert tested.json() == {"ok": True, "detail": "A token was issued"}
    assert _activate(process_id, deployment["id"]).status_code == 200

    tool = _schema_tool(process_id, deployment["id"])
    lookup = (tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})
    fake_llm.complete.side_effect = [_result(calls=[lookup]), _result(calls=[(tool, {"operation": "getCustomer",
                                     "params": {"customerId": "C-2"}})]), _result(text_='{"summary": "ok"}')]
    token = _client_token(client)
    execution_id = _start(token, deployment["id"], {}).json()["id"]
    _run_jobs()

    token_requests = [r for r in systems.requests if str(r.url) == TOKEN_URL]
    assert len(token_requests) == 1  # the test-credential call cached it
    assert parse_qs(token_requests[0].content.decode())["scope"] == ["core.read"]
    calls = [r for r in systems.requests if r.url.host == "api.bank.example"]
    assert len(calls) == 2 and all(r.headers["Authorization"] == "Bearer tok-core.read" for r in calls)

    # Nothing the run wrote, and nothing the model saw, holds a secret.
    with get_owner_engine().connect() as conn:
        written = json.dumps([list(map(str, row)) for table in ("agent_executions", "execution_steps", "audit_events",
                              "adapter_bindings") for row in conn.execute(text(f"SELECT * FROM {table}"))])
    sent = json.dumps([str(c) for c in fake_llm.complete.call_args_list])
    for value in (SECRET, "tok-core.read"):
        assert value not in written and value not in sent
    assert execution_id in written


# ---- the kill switch --------------------------------------------------------------------


def test_kill_stops_running_and_waiting_work_and_refuses_new(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    tool = _schema_tool(process_id, deployment["id"])
    kill_url = f"/api/processes/{process_id}/deployments/{deployment['id']}/kill"

    def kill_during_the_call(request):
        # The kill lands while the tool call is in flight.
        with get_owner_engine().begin() as conn:
            conn.execute(text("UPDATE agent_deployments SET status = 'killed', stop_reason = 'Wrong rates' "
                              "WHERE id = :i"), {"i": deployment["id"]})

    systems.on_request = kill_during_the_call
    fake_llm.complete.side_effect = [
        _result(calls=[(tool, {"operation": "getCustomer", "params": {"customerId": "C-1"}})]),
        _result(text_='{"summary": "never reached"}'),
    ]
    first = _start(token, deployment["id"], {}, key="in-flight").json()["id"]
    waiting = [_start(token, deployment["id"], {}, key=f"waiting-{i}").json()["id"] for i in range(20)]
    # Run only the first; the others wait in the queue.
    session = get_session_factory()()
    try:
        from app.jobs import queue
        from app.jobs.worker import run_job
        from app.tenancy.context import tenant_scope

        with tenant_scope(TEST_TENANT_ID):
            job = queue.claim(session, "test", kinds=["runtime_execution"], limit=1)[0]
            session.commit()
            asyncio.run(run_job(job.id, TEST_TENANT_ID))
    finally:
        session.close()

    with get_owner_engine().connect() as conn:
        status = conn.scalar(text("SELECT status FROM agent_executions WHERE id = :i"), {"i": first})
        steps = [r[0] for r in conn.execute(text("SELECT type FROM execution_steps WHERE execution_id = :i ORDER BY seq"),
                                            {"i": first})]
    assert status == "KILLED"
    assert steps[-3:] == ["POLICY_CHECK", "TOOL_EXECUTED", "RUN_COMPLETED"]  # the call finished; nothing after it
    assert steps.count("MODEL_INVOKED") == 1

    # The kill through the API (here already applied) stops what's waiting.
    with get_owner_engine().begin() as conn:
        conn.execute(text("UPDATE agent_deployments SET status = 'active' WHERE id = :i"), {"i": deployment["id"]})
    killed = client.post(kill_url, json={"reason": "Wrong rates"})
    assert killed.status_code == 200 and killed.json()["waiting_stopped"] == 20
    assert _run_jobs() == 0
    with get_owner_engine().connect() as conn:
        statuses = {r[0] for r in conn.execute(text("SELECT status FROM agent_executions WHERE id = ANY(:ids)"),
                                               {"ids": waiting})}
        stepped = conn.scalar(text("SELECT count(*) FROM execution_steps WHERE execution_id = ANY(:ids)"), {"ids": waiting})
    assert statuses == {"KILLED"} and stepped == 0
    refused = _start(token, deployment["id"], {}, key="after")
    assert refused.status_code == 503 and "Wrong rates" in refused.json()["detail"]
    # A killed deployment never comes back.
    assert _activate(process_id, deployment["id"]).status_code == 409


def test_kill_needs_a_reason_and_the_permission(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    url = f"/api/processes/{process_id}/deployments/{deployment['id']}/kill"
    assert client.post(url, json={"reason": ""}).status_code == 422
    analyst = TestClient(app)
    sign_in(analyst, "runtime-analyst", ["analyst"])
    assert analyst.post(url, json={"reason": "x"}).status_code == 403


def test_tenant_wide_kill_and_identity_revocation(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm)
    stopped = client.post("/api/runtime/kill", json={"reason": "Incident 42"})
    assert stopped.status_code == 200 and stopped.json()["deployments"] == 1
    assert _start(token, deployment["id"], {}).status_code == 503

    # A killed deployment stays killed; the same approved version is deployed afresh.
    second = _deploy(client, process_id, deployment["agent_artifact_id"])
    _bind(client, process_id, second)
    assert _activate(process_id, second["id"]).status_code == 200
    revoked = client.post(f"/api/processes/{process_id}/deployments/{second['id']}/identity/revoke").json()
    assert revoked["identity"]["status"] == "revoked" and revoked["status"] == "killed"
    assert _start(token, second["id"], {}).status_code == 503


def test_operator_stops_a_tenants_agents_without_data_access(client, fake_llm):
    from app.operators import tenants as operator_tenants

    process_id, deployment, _ = _live(client, fake_llm)
    result = operator_tenants.stop_agents(TEST_TENANT_ID, reason="Provider outage", operator_id="op1",
                                          operator_name="On-call")
    assert result == {"deployments": 1, "waiting_stopped": 0, "running": 0}
    detail = client.get(f"/api/processes/{process_id}/deployments/{deployment['id']}").json()
    assert detail["status"] == "killed" and detail["stop_reason"] == "Provider outage"
    with get_owner_engine().connect() as conn:
        actor = conn.execute(text("SELECT actor_type, actor_name FROM audit_events "
                                  "WHERE action = 'runtime_stopped_by_operator'")).one()
    assert tuple(actor) == ("operator", "On-call")


# ---- restarts ---------------------------------------------------------------------------


def test_a_restart_after_a_sent_mutating_call_escalates(client, fake_llm, systems):
    process_id, deployment, token = _live(client, fake_llm, writes=True)
    execution_id = _start(token, deployment["id"], {}).json()["id"]
    # A worker died right after the gateway let a data-changing call through.
    with get_session_factory()() as session:
        from app.tenancy.context import tenant_scope

        with tenant_scope(TEST_TENANT_ID):
            execution = session.get(AgentExecutionModel, execution_id)
            execution.attempt, execution.status = 1, "WAITING_FOR_TOOL"
            session.add(ExecutionStepModel(id="xs1", execution_id=execution_id, attempt=1, seq=2, type="POLICY_CHECK",
                                           target="Core banking", step={"denied": False}, occurred_at=execution.created_at))
            session.commit()
    _run_jobs()
    with get_owner_engine().connect() as conn:
        status, deviations = conn.execute(text("SELECT status, deviations FROM agent_executions WHERE id = :i"),
                                          {"i": execution_id}).one()
    assert status == "ESCALATED" and "outcome is unknown" in deviations[-1]["reason"]
    assert not [r for r in systems.requests if r.url.host == "api.bank.example"]
    assert fake_llm.complete.await_count == 0 or not any(
        c.kwargs.get("operation") == "runtime_execution" for c in fake_llm.complete.call_args_list)


# ---- tenants ----------------------------------------------------------------------------


def test_another_tenants_client_cant_start_this_tenants_agent(client, fake_llm):
    process_id, deployment, _ = _live(client, fake_llm)
    del process_id
    make_tenant("ten_rt_b", "rtb", ["tenant-rtb.test"])
    other = TestClient(app, base_url="http://tenant-rtb.test")
    sign_in(other, "b-admin", ALL_ROLES)
    token_b = other.post("/api/runtime/clients", json={"label": "B"}).json()["token"]
    assert _start(token_b, deployment["id"], {}, host="tenant-rtb.test").status_code == 404
    with get_owner_engine().connect() as conn:
        assert conn.scalar(select(AgentExecutionModel.id).where(AgentExecutionModel.deployment_id == deployment["id"])) is None
