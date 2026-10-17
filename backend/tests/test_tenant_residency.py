"""Epic 38, US38.15: data residency, one deployment per region.

With DEPLOYMENT_REGION set, tenants are created only in this region, a
tenant of another region isn't served here, and model calls go only to a
provider hosted in it. Unset, nothing changes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.config import get_settings
from app.db.models import TenantModel
from app.db.session import get_owner_engine, get_platform_session_factory
from app.llm.client import LLMClient
from app.llm.exceptions import LLMPolicyError
from app.llm.routing import LlmRoute, current_route
from app.llm.types import ChatMessage
from app.main import app
from app.tenancy import tenant_scope

from .conftest import TEST_TENANT_ID, make_tenant

OPS = "http://ops.localhost"
US_CONSOLE = "https://ops.us.axyntro.example"


@pytest.fixture
def eu(monkeypatch):
    monkeypatch.setenv("OPERATOR_AUTH_MODE", "local")
    monkeypatch.setenv("DEPLOYMENT_REGION", "EU")
    monkeypatch.setenv("REGION_DEPLOYMENTS", f'{{"US": "{US_CONSOLE}", "EU": "https://ops.eu.axyntro.example"}}')
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def _operator() -> TestClient:
    ops = TestClient(app, base_url=OPS)
    response = ops.post("/api/operator/login", json={"name": "Otto Operator"})
    ops.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return ops


def _create(ops: TestClient, slug: str, region: str | None):
    return ops.post("/api/operator/tenants", json={"slug": slug, "host": f"{slug}.test", "region": region,
                                                   "first_admin_email": f"admin@{slug}.test"})


def test_tenants_are_created_in_this_deployments_region_only(eu):
    ops = _operator()
    config = ops.get("/api/operator/config").json()
    assert config["region"] == "EU" and config["regions"]["US"] == US_CONSOLE
    refused = _create(ops, "acme-us", "US")
    assert refused.status_code == 400 and US_CONSOLE in refused.json()["detail"]
    assert _create(ops, "acme", None).json()["tenant"]["region"] == "EU"
    assert _create(ops, "beta", "eu").json()["tenant"]["region"] == "EU"


def test_another_regions_tenant_is_not_served_here(eu):
    make_tenant("ten_us", "us-co", ["us-co.test"])
    with get_owner_engine().begin() as conn:
        conn.execute(update(TenantModel).where(TenantModel.id == "ten_us").values(region="US"))
    response = TestClient(app, base_url="http://us-co.test").get("/api/auth/config")
    assert response.status_code == 421 and "region" in response.json()["detail"]
    # A tenant from before the deployment had a region, and this region's, are served.
    assert TestClient(app).get("/api/auth/config").status_code == 200
    with get_owner_engine().begin() as conn:
        conn.execute(update(TenantModel).where(TenantModel.id == TEST_TENANT_ID).values(region="eu"))
    assert TestClient(app).get("/api/auth/config").status_code == 200


def _fake_provider(monkeypatch) -> AsyncMock:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")],
        model="test-model", usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    create = AsyncMock(return_value=response)
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), base_url="https://p/")
    monkeypatch.setattr(LLMClient, "_client_and_payer", lambda self, route: (fake, "platform"))
    return create


def _call(llm: LLMClient, route: LlmRoute | None = None):
    token = current_route.set(route)
    try:
        with tenant_scope(TEST_TENANT_ID):
            return asyncio.run(llm.complete([ChatMessage(role="user", content="hi")], operation="op"))
    finally:
        current_route.reset(token)


def test_model_calls_stay_in_the_region(eu, monkeypatch):
    create = _fake_provider(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER_REGION", "US")
    get_settings.cache_clear()
    with pytest.raises(LLMPolicyError, match="stays in region EU.*hosted in US"):
        _call(LLMClient())
    monkeypatch.setenv("LLM_PROVIDER_REGION", "")
    get_settings.cache_clear()
    with pytest.raises(LLMPolicyError, match="unknown hosting region"):
        _call(LLMClient())
    assert create.await_count == 0  # refused before reaching the provider

    # A self-hosted endpoint in the region is fine, whatever the default provider is.
    monkeypatch.setenv("LLM_PROVIDER_REGION", "US")
    get_settings.cache_clear()
    assert _call(LLMClient(), LlmRoute(base_url="https://llm.eu.example/v1", provider_region="EU")).text == "hi"
    with pytest.raises(LLMPolicyError):
        _call(LLMClient(), LlmRoute(base_url="https://llm.example/v1"))
    monkeypatch.setenv("LLM_PROVIDER_REGION", "eu")
    get_settings.cache_clear()
    assert _call(LLMClient()).text == "hi"


def test_without_a_deployment_region_nothing_changes(monkeypatch):
    create = _fake_provider(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER_REGION", "US")
    get_settings.cache_clear()
    try:
        assert _call(LLMClient()).text == "hi" and create.await_count == 1
        make_tenant("ten_any", "anywhere", ["anywhere.test"])
        with get_owner_engine().begin() as conn:
            conn.execute(update(TenantModel).where(TenantModel.id == "ten_any").values(region="APAC"))
        assert TestClient(app, base_url="http://anywhere.test").get("/api/auth/config").status_code == 200
        with get_platform_session_factory()() as db:
            assert db.get(TenantModel, "ten_any").region == "APAC"
    finally:
        get_settings.cache_clear()
