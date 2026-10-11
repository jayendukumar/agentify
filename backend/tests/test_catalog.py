"""Epic 29: application and data catalog."""

import asyncio
import json

import httpx
import pytest

from app.catalog.endpoints import UnsafeEndpointError, check_endpoint
from app.catalog.openapi import build_tool_schema, load_spec, parse_operations
from app.db import repository
from app.db.models import AgentArtifactModel
from app.db.session import get_session_factory
from app.twin import simulators
from app.twin.errors import TwinServiceError
from tests.test_api_blueprint import _finalize, _llm_json, _node_result

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Core Banking API", "version": "2.1"},
    "paths": {
        "/customers/{customerId}": {
            "get": {
                "operationId": "getCustomer",
                "summary": "Fetch a customer",
                "parameters": [{"name": "customerId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Customer"}}}}},
            }
        },
        "/accounts": {
            "post": {
                "summary": "Open an account",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"customerId": {"type": "string"}}}}}},
                "responses": {"201": {"content": {"application/json": {"schema": {"type": "object", "properties": {"accountId": {"type": "string"}}}}}}},
            }
        },
    },
    "components": {"schemas": {"Customer": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "riskRating": {"type": "string"}}}}},
}


def _create(client, **body):
    response = client.post("/api/organization/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    "url,env",
    [
        ("http://localhost:9000", "sandbox"),
        ("https://api.sandbox.bank.example", "sandbox"),
        ("https://corebanking-test.internal.example", "test"),
        ("http://mockserver.local/api", "mock"),
    ],
)
def test_safe_endpoints_pass(url, env):
    check_endpoint(url, env)


@pytest.mark.parametrize(
    "url,env,reason",
    [
        ("https://api.sandbox.bank.example", None, "no environment tag"),
        ("https://api.sandbox.bank.example", "production", "tagged 'production'"),
        ("https://prod-api.bank.example", "sandbox", "looks like production"),
        ("https://api.bank.example", "sandbox", "not recognisably non-production"),
        ("https://live.sandbox.bank.example", "sandbox", "looks like production"),
        ("ftp://test.example", "test", r"not an http\(s\) URL"),
    ],
)
def test_unsafe_endpoints_refused(url, env, reason):
    with pytest.raises(UnsafeEndpointError, match=reason):
        check_endpoint(url, env)


def test_allow_list_setting(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "twin_api_allowed_hosts", "*.partner-uat.example, api.bank.example")
    check_endpoint("https://api.bank.example", "staging")
    # A production-looking host is refused even if allow-listed.
    with pytest.raises(UnsafeEndpointError):
        check_endpoint("https://prod.partner-uat.example", "staging")


def test_openapi_parsing_and_tool_schema():
    spec = load_spec(json.dumps(SPEC))
    ops = parse_operations(spec)
    assert [o.operation_id for o in ops] == ["getCustomer", "post_accounts"]
    assert ops[0].parameters["required"] == ["customerId"]
    assert ops[0].response_schema["properties"]["riskRating"] == {"type": "string"}  # $ref resolved
    assert "body" in ops[1].parameters["properties"]
    schema = build_tool_schema("Core banking", "app-1", ops, base_url="http://localhost:9000", environment="sandbox")
    assert schema.source == "openapi"
    assert schema.parameters["properties"]["operation"]["enum"] == ["getCustomer", "post_accounts"]
    assert "GET /customers/{customerId}" in schema.description
    assert schema.api_operations["getCustomer"] == {"method": "GET", "path": "/customers/{customerId}"}
    with pytest.raises(Exception):
        load_spec("not: [an, openapi]")


def test_csv_import_preview_then_commit_with_alias_dedup(client):
    _create(client, kind="application", name="Salesforce", data={"aliases": ["CRM"]})
    csv = (
        "Application Name,Aliases,Vendor,Business Criticality\n"
        "Core Banking Platform,core banking;CBP,Temenos,Tier 1\n"
        "CRM,,Salesforce Inc,High\n"
        "core banking platform,,,\n"
        ",,,\n"
    ).encode()
    preview = client.post(
        "/api/organization/applications/import", files={"file": ("apps.csv", csv, "text/csv")}
    ).json()
    assert preview["dry_run"] is True
    assert [r["action"] for r in preview["rows"]] == ["create", "update", "skip", "skip"]
    assert preview["rows"][1]["message"] == "matches existing 'Salesforce'"
    assert preview["rows"][2]["message"] == "duplicate of row 2"
    assert len(client.get("/api/organization/entities?kind=application").json()) == 1  # nothing written

    result = client.post(
        "/api/organization/applications/import?dry_run=false", files={"file": ("apps.csv", csv, "text/csv")}
    ).json()
    assert result["created"] == 1 and result["updated"] == 1
    apps = {a["name"]: a for a in client.get("/api/organization/entities?kind=application").json()}
    assert apps["Core Banking Platform"]["data"]["aliases"] == ["core banking", "CBP"]
    assert apps["Core Banking Platform"]["data"]["criticality"] == "critical"
    assert apps["Salesforce"]["data"]["vendor"] == "Salesforce Inc"

    bad = client.post("/api/organization/applications/import", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert bad.status_code == 400 and "No name column" in bad.json()["detail"]


def _blueprint_with_systems(client, fake_llm, systems, outputs=None):
    process = client.post("/api/processes", json={"name": "Onboarding"}).json()
    _finalize(client, process["id"])
    spec = {
        "name": "Onboarding Agent",
        "purpose": "Open the account",
        "trigger": "Application approved",
        "tools_systems_needed": systems,
        "expected_outputs": outputs or [],
    }
    fake_llm.complete.return_value = _llm_json(
        {
            "nodes": [
                _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="entry"),
                _node_result("Task_a", agent_spec=spec),
                _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="exit"),
            ]
        }
    )
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    return process


def test_system_resolution_auto_links_exact_and_offers_ambiguous(client, fake_llm):
    core = _create(client, kind="application", name="Core Banking Platform", data={"aliases": ["core banking"]})
    _create(client, kind="application", name="KYC Screening Service")
    _create(client, kind="application", name="KYC Document Store")
    process = _blueprint_with_systems(client, fake_llm, ["Core banking", "KYC system", "Fax machine"])

    resolutions = {r["system_name"]: r for r in client.get(f"/api/processes/{process['id']}/systems").json()}
    assert resolutions["Core banking"]["status"] == "matched" and resolutions["Core banking"]["auto"] is True
    assert resolutions["Core banking"]["application_id"] == core["id"]
    assert resolutions["KYC system"]["status"] == "ambiguous"
    assert {c["name"] for c in resolutions["KYC system"]["candidates"]} == {"KYC Screening Service", "KYC Document Store"}
    assert resolutions["Fax machine"]["status"] == "unmatched"
    assert resolutions["Core banking"]["used_in"] == ["blueprint agent 'Onboarding Agent'"]

    kyc = resolutions["KYC system"]["candidates"][0]["id"]
    after = {r["system_name"]: r for r in client.put(f"/api/processes/{process['id']}/systems/KYC system", json={"application_id": kyc}).json()}
    assert after["KYC system"]["status"] == "linked" and after["KYC system"]["auto"] is False
    # "Not in the catalog" is a decision too.
    after = {r["system_name"]: r for r in client.put(f"/api/processes/{process['id']}/systems/Fax machine", json={"application_id": None}).json()}
    assert after["Fax machine"]["status"] == "unmatched" and after["Fax machine"]["candidates"] == []


def test_agent_permissions_follow_data_classes(client, fake_llm):
    pii = _create(client, kind="data_asset", name="Customer", data={"classification": "Confidential-PII", "residency": ["EU"]})
    restricted = _create(client, kind="data_asset", name="Sanctions hits", data={"classification": "Restricted"})
    _create(client, kind="application", name="Core banking", data={"data_assets": [pii["id"]]})
    _create(client, kind="application", name="CRM", data={"data_assets": [pii["id"]]})
    _create(client, kind="application", name="Screening", data={"data_assets": [restricted["id"]]})
    process = _blueprint_with_systems(
        client,
        fake_llm,
        ["Core banking", "CRM", "Screening", "Email"],
        outputs=[{"name": "account_id", "source_or_destination": "Core banking", "format": "string"}],
    )
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()
    definition = artifact["definition"]
    contracts = {c["system_name"]: c for c in definition["tool_contracts"]}
    permissions = {p["resource"]: p["actions"] for p in definition["permissions"]}

    assert contracts["Core banking"]["application_id"] and contracts["Core banking"]["data_classifications"] == ["Confidential-PII"]
    # Writes to Core banking (an output goes there) -> update granted.
    assert contracts["Core banking"]["action"] == "update" and permissions["Core banking"]["update"] is True
    # Only reads CRM -> read-only on PII.
    assert permissions["CRM"] == {"read": True, "update": False, "initiate": False, "override": False}
    # Restricted data is never granted by default.
    assert permissions["Screening"]["read"] is False
    # Unlinked system keeps Epic 20's default.
    assert contracts["Email"]["application_id"] is None and permissions["Email"]["read"] is True

    register = {r["application"]: r for r in client.get("/api/organization/applications/register").json()}
    assert register["Core banking"]["agents"][0]["name"] == "Onboarding Agent"
    assert register["Core banking"]["classifications"] == ["Confidential-PII"]


def test_spec_backed_systems_skip_llm_inference(client, fake_llm):
    app = _create(client, kind="application", name="Core banking")
    upload = client.put(
        f"/api/organization/applications/{app['id']}/api-spec",
        files={"file": ("spec.json", json.dumps(SPEC).encode(), "application/json")},
    )
    assert upload.status_code == 200 and upload.json()["title"] == "Core Banking API"
    process = _blueprint_with_systems(client, fake_llm, ["Core banking", "Email"])
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()

    fake_llm.complete.reset_mock()
    fake_llm.complete.return_value = _llm_json(
        {"tools": [{"system_name": "Email", "tool_name": "send_email", "description": "Send", "parameters": {"type": "object"}, "response_shape_description": "{}"}]}
    )
    session = get_session_factory()()
    try:
        model = session.get(AgentArtifactModel, artifact["id"])
        schemas = asyncio.run(repository._get_or_infer_tool_schemas(fake_llm, session, model))
        session.commit()
    finally:
        session.close()
    assert schemas["Core banking"].source == "openapi"
    assert schemas["Email"].source == "inferred"
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert "- Email" in prompt and "Core banking" not in prompt


def test_endpoint_registration_refuses_unsafe(client):
    app = _create(client, kind="application", name="Core banking")
    ok = client.put(
        f"/api/organization/applications/{app['id']}/endpoints",
        json=[{"url": "http://localhost:9000", "environment": "sandbox"}, {"url": "https://api.bank.example", "environment": "production"}],
    )
    assert ok.status_code == 200 and len(ok.json()["data"]["endpoints"]) == 2
    refused = client.put(
        f"/api/organization/applications/{app['id']}/endpoints",
        json=[{"url": "https://prod-api.bank.example", "environment": "sandbox"}],
    )
    assert refused.status_code == 400 and "looks like production" in refused.json()["detail"]


def test_api_mode_calls_sandbox_and_refuses_production(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"id": "c-1", "name": "Ada", "riskRating": "low"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(simulators.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    ops = parse_operations(load_spec(json.dumps(SPEC)))
    schema = build_tool_schema("Core banking", "app-1", ops, base_url="http://localhost:9000/v2", environment="sandbox")

    response = asyncio.run(simulators._call_api(schema, {"operation": "getCustomer", "params": {"customerId": "c 1"}}))
    assert response["name"] == "Ada"
    assert calls == [("GET", "http://localhost:9000/v2/customers/c%201")]

    prod = schema.model_copy(update={"api_base_url": "https://prod.bank.example", "api_environment": "sandbox"})
    with pytest.raises(TwinServiceError, match="looks like production"):
        asyncio.run(simulators._call_api(prod, {"operation": "getCustomer", "params": {"customerId": "1"}}))
    no_endpoint = schema.model_copy(update={"api_base_url": None})
    with pytest.raises(TwinServiceError, match="no registered non-production endpoint"):
        asyncio.run(simulators._call_api(no_endpoint, {"operation": "getCustomer", "params": {}}))
    assert len(calls) == 1
