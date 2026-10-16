"""Epic 38, US38.11: each tenant's model policy, registries and provider key.

A tenant's model policy applies to its own calls only; its registries are
its own records; it can call the default provider on its own key, and its
usage is recorded either way, with the monthly budget counting only the
hosting company's key."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.config import RegistryConfig, get_settings
from app.db.models import LlmUsageEventModel
from app.db.session import get_owner_engine, get_session_factory
from app.llm import metering
from app.llm.client import LLMClient
from app.llm.exceptions import LLMBudgetError, LLMPolicyError
from app.llm.routing import route_for_scope
from app.llm.types import ChatMessage, Usage
from app.main import app
from app.tenancy import tenant_scope
from app.tenancy.limits import forget_cached_limits

from .conftest import TEST_TENANT_ID, sign_in
from .test_tenant_identity import NORTHWIND, NORTHWIND_ID, northwind_admin  # noqa: F401

PRICED = "qwen/qwen3.7-flash"
KEY_REF = "secret://tenant/llm_provider_key"


# ---- the model policy is the tenant's --------------------------------------------------


def test_each_tenants_model_policy_applies_to_its_own_calls_only(client, northwind_admin):  # noqa: F811
    eu_only = {"residency": ["EU"]}
    assert client.put("/api/organization/llm-policy", json=eu_only).status_code == 200
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        assert "requires models hosted in EU" in route_for_scope(session, None).blocked_reason
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as session:
        assert route_for_scope(session, None) is None  # Northwind set none: calls go out as configured
    # Business units inherit their own tenant's policy, as before.
    unit = northwind_admin.post("/api/organization/entities", json={
        "kind": "business_unit", "name": "Claims", "code": "CLM", "data": {}})
    assert unit.status_code in (200, 201), unit.text
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as session:
        assert route_for_scope(session, unit.json()["id"]) is None


# ---- registries are the tenant's records -----------------------------------------------


def test_registries_default_per_tenant_and_admins_set_their_own(client, northwind_admin, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "registries", [RegistryConfig(name="enterprise", type="local")])
    ours = client.get("/api/organization/registries").json()
    assert (ours["source"], [r["name"] for r in ours["registries"]]) == ("environment", ["enterprise"])
    theirs = northwind_admin.get("/api/organization/registries").json()
    assert (theirs["source"], [r["name"] for r in theirs["registries"]]) == ("default", ["local"])

    saved = northwind_admin.put("/api/organization/registries", json={"registries": [
        {"name": "claims-team", "type": "local"}, {"name": "finance-team", "type": "local"}]})
    assert saved.status_code == 200, saved.text
    assert [r["name"] for r in northwind_admin.get("/api/registries").json()] == ["claims-team", "finance-team"]
    assert [r["name"] for r in client.get("/api/registries").json()] == ["enterprise"]  # ours unchanged

    pushed = northwind_admin.post("/api/registries/claims-team/push",
                                  json={"agent_name": "Northwind Claims Agent", "definition": {}, "tags": ["claims"]})
    assert pushed.status_code == 200, pushed.text
    assert northwind_admin.get("/api/registries/search", params={"q": "Claims"}).json()["entries"]
    assert client.get("/api/registries/search", params={"q": "Claims"}).json()["entries"] == []
    assert client.post("/api/registries/claims-team/push", json={"agent_name": "x", "definition": {}}).status_code == 404

    # Back to the default with an empty list.
    northwind_admin.put("/api/organization/registries", json={"registries": []})
    assert northwind_admin.get("/api/organization/registries").json()["source"] == "default"


@pytest.mark.parametrize(
    ("registries", "reason"),
    [
        ([{"name": "x", "type": "vendor-x"}], "Unknown registry type"),
        ([{"name": "x", "type": "local"}, {"name": "x", "type": "local"}], "its own name"),
        ([{"name": "x", "type": "local", "config": {"api_token": "abc"}}], "reference the secret"),
        ([{"name": "x", "type": "local", "config": {"token_ref": "env:OPENROUTER_API_KEY"}}], "first organization"),
    ],
)
def test_registry_settings_that_could_leak_are_refused(northwind_admin, registries, reason):  # noqa: F811
    response = northwind_admin.put("/api/organization/registries", json={"registries": registries})
    assert response.status_code == 400 and reason in response.json()["detail"]


def test_only_admins_change_registries(northwind_admin):  # noqa: F811
    analyst = TestClient(app, base_url=f"http://{NORTHWIND}")
    sign_in(analyst, "Ann Analyst", ["analyst"])
    assert analyst.put("/api/organization/registries", json={"registries": []}).status_code == 403


# ---- the provider key: the platform's or the tenant's own ------------------------------


def _fake_provider(client):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")],
        model=PRICED, usage=SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000))
    client.chat.completions.create = AsyncMock(return_value=response)
    return client.chat.completions.create


def test_a_tenant_can_bring_its_own_provider_key(northwind_admin, monkeypatch):  # noqa: F811
    refused = northwind_admin.put("/api/organization/llm-provider-key", json={"key_ref": "env:OPENROUTER_API_KEY"})
    assert refused.status_code == 400 and "first organization" in refused.json()["detail"]
    status = northwind_admin.put("/api/organization/llm-provider-key", json={"key_ref": KEY_REF}).json()
    assert status["key_set"] is False and status["location"].startswith("TENANT_NORTHWIND_LLM_PROVIDER_KEY")

    llm = LLMClient()
    with tenant_scope(NORTHWIND_ID):
        # Set but missing: refused, never quietly put on the platform's key.
        with pytest.raises(LLMPolicyError, match="TENANT_NORTHWIND_LLM_PROVIDER_KEY"):
            asyncio.run(llm.complete([ChatMessage(role="user", content="hi")], operation="op"))
    monkeypatch.setenv("TENANT_NORTHWIND_LLM_PROVIDER_KEY", "sk-northwind-own")
    assert northwind_admin.get("/api/organization/llm-provider-key").json()["key_set"] is True
    with tenant_scope(NORTHWIND_ID):
        own, payer = llm._client_and_payer(None)
        assert (own.api_key, payer) == ("sk-northwind-own", "tenant")
        assert str(own.base_url) == str(llm._client.base_url)  # the same provider, another key
    with tenant_scope(TEST_TENANT_ID):
        assert llm._client_and_payer(None) == (llm._client, "platform")

    assert northwind_admin.put("/api/organization/llm-provider-key", json={"key_ref": None}).json()["key_ref"] is None
    with tenant_scope(NORTHWIND_ID):
        assert llm._client_and_payer(None)[1] == "platform"


def test_usage_is_the_tenants_either_way_and_the_budget_counts_the_platforms_key(northwind_admin, monkeypatch):  # noqa: F811
    with get_owner_engine().begin() as conn:
        conn.execute(text("UPDATE tenants SET llm_monthly_budget_usd = 0.02 WHERE id = :id"), {"id": NORTHWIND_ID})
    forget_cached_limits()
    llm = LLMClient()
    with tenant_scope(NORTHWIND_ID):
        _fake_provider(llm._client)
        asyncio.run(llm.complete([ChatMessage(role="user", content="hi")], operation="op"))  # $0.03, platform's key
        with pytest.raises(LLMBudgetError):  # the platform's quota is spent
            asyncio.run(llm.complete([ChatMessage(role="user", content="hi")], operation="op"))

    monkeypatch.setenv("TENANT_NORTHWIND_LLM_PROVIDER_KEY", "sk-northwind-own")
    northwind_admin.put("/api/organization/llm-provider-key", json={"key_ref": KEY_REF})
    with tenant_scope(NORTHWIND_ID):
        own, _ = llm._client_and_payer(None)
        create = _fake_provider(own)
        asyncio.run(llm.complete([ChatMessage(role="user", content="hi")], operation="op"))  # on its own key: allowed
        assert create.await_count == 1
        with get_session_factory()() as session:
            sources = sorted(session.scalars(select(LlmUsageEventModel.key_source)))
            assert sources == ["platform", "tenant"]  # both are Northwind's usage
            assert metering.month_spend(session) == pytest.approx(0.03)  # the platform's key only
            assert metering.month_spend(session, key_source=None) == pytest.approx(0.06)
            assert metering.budget_reached(session) is False  # batches keep going on the tenant's key

    overview = northwind_admin.get("/api/usage").json()
    assert overview["budget"]["own_key"] is True and overview["budget"]["spent_usd"] == pytest.approx(0.03)
    [month] = overview["months"]
    assert month["cost_usd"] == pytest.approx(0.06) and month["own_key_cost_usd"] == pytest.approx(0.03)
    assert {op["key_source"] for op in month["operations"]} == {"platform", "tenant"}


def test_a_self_hosted_endpoint_is_on_the_tenants_side(monkeypatch):
    monkeypatch.setenv("TENANT_TEST_LLM_API_KEY", "sk-endpoint")
    from app.llm.routing import LlmRoute

    with tenant_scope(TEST_TENANT_ID):
        _, payer = LLMClient()._client_and_payer(LlmRoute(base_url="https://llm.internal/v1",
                                                          api_key_env="secret://tenant/llm_api_key"))
    assert payer == "tenant"
    usage = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
    with tenant_scope(TEST_TENANT_ID):
        asyncio.run(metering.record(operation="op", model="local/llama", usage=usage, key_source=payer))
        with get_session_factory()() as session:
            assert session.scalars(select(LlmUsageEventModel.key_source)).one() == "tenant"
