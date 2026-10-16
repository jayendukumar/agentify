"""Epic 38, US38.10: each tenant's secrets, files and log lines kept apart.

A secret reference reaches only its own tenant's secrets (never the
platform's, never another tenant's); uploaded files land in the tenant's
own directory, whatever the upload calls itself; every log line says
which tenant it ran as."""

import importlib.util
import logging
from pathlib import Path

import pytest

from app import secret_store
from app.config import get_settings
from app.connectors.base import Connector, ConnectorError
from app.llm.client import LLMClient
from app.llm.exceptions import LLMPolicyError
from app.llm.routing import LlmRoute
from app.request_context import RequestIDLogFilter
from app.tenancy import tenant_scope
from app.tenancy.files import TenantFileError, safe_name, tenant_dir, tenant_file

from .conftest import TEST_TENANT_ID
from .test_tenant_identity import NORTHWIND_ID, northwind_admin  # noqa: F401

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(autouse=True)
def _env_backend():
    secret_store.get_store.cache_clear()
    yield
    secret_store.get_store.cache_clear()


# ---- secret references -------------------------------------------------------------


def test_a_reference_reaches_only_its_own_tenants_secret(monkeypatch):
    monkeypatch.setenv("TENANT_NORTHWIND_CRM_TOKEN", "northwind-token")
    monkeypatch.setenv("TENANT_TEST_CRM_TOKEN", "test-token")
    assert secret_store.resolve("secret://tenant/crm_token", "northwind") == "northwind-token"
    assert secret_store.resolve("secret://tenant/crm_token", "test") == "test-token"
    assert secret_store.resolve("secret://tenant/crm_token", None) is None  # no tenant, no secret
    # Nothing in a name climbs into another tenant's or the platform's.
    for sneaky in ("secret://tenant/../test/crm_token", "secret://tenant/TEST_CRM_TOKEN", "secret://test/crm_token"):
        assert secret_store.resolve(sneaky, "northwind") is None


def test_environment_names_are_for_the_first_tenant_and_the_platform_only(monkeypatch):
    monkeypatch.setenv("SOME_PLATFORM_KEY", "platform")
    assert secret_store.resolve("env:SOME_PLATFORM_KEY", "test") == "platform"  # the first tenant (conftest)
    assert secret_store.resolve("env:SOME_PLATFORM_KEY", secret_store.PLATFORM) == "platform"
    assert secret_store.resolve("env:SOME_PLATFORM_KEY", "northwind") is None
    with pytest.raises(secret_store.SecretRefError, match="first organization"):
        secret_store.check_ref("env:SOME_PLATFORM_KEY", "northwind")
    with pytest.raises(secret_store.SecretRefError, match="not the secret itself"):
        secret_store.check_ref("sk-live-123", "test")


def test_the_files_backend_reads_mounted_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "secrets_backend", "files")
    monkeypatch.setattr(get_settings(), "secrets_dir", str(tmp_path))
    (tmp_path / "TENANT_NORTHWIND_CRM_TOKEN").write_text("from-a-file\n")
    assert secret_store.resolve("secret://tenant/crm_token", "northwind") == "from-a-file"
    assert secret_store.resolve("secret://tenant/crm_token", "test") is None
    assert secret_store.where_to_put("secret://tenant/crm_token", "test") == str(tmp_path / "TENANT_TEST_CRM_TOKEN")


class _Probe(Connector):
    type = "probe"

    async def fetch(self):  # pragma: no cover -- only secret() is exercised
        raise NotImplementedError


def test_a_connector_token_is_its_tenants_own(monkeypatch, northwind_admin):  # noqa: F811
    monkeypatch.setenv("TENANT_NORTHWIND_CMDB_TOKEN", "nw-cmdb")
    monkeypatch.setenv("OPENROUTER_API_KEY", "the-platforms-key")
    with tenant_scope(NORTHWIND_ID):
        assert _Probe({"token_ref": "secret://tenant/cmdb_token"}).secret() == "nw-cmdb"
        # Saved before US38.10: an environment name in another tenant no longer resolves.
        with pytest.raises(ConnectorError, match="first organization"):
            _Probe({"token_env": "OPENROUTER_API_KEY"}).secret()
        with pytest.raises(ConnectorError, match="TENANT_NORTHWIND_MISSING"):
            _Probe({"token_ref": "secret://tenant/missing"}).secret()

    refused = northwind_admin.post("/api/organization/connectors", json={
        "name": "cmdb", "type": "http", "config": {"url": "https://cmdb.example.com", "token_env": "OPENROUTER_API_KEY"}})
    assert refused.status_code == 400 and "first organization" in refused.json()["detail"]
    saved = northwind_admin.post("/api/organization/connectors", json={
        "name": "cmdb", "type": "http", "config": {"url": "https://cmdb.example.com", "token_ref": "secret://tenant/cmdb_token"}})
    assert saved.status_code in (200, 201), saved.text


def test_the_first_tenant_keeps_its_environment_names(client, monkeypatch):
    monkeypatch.setenv("CMDB_TOKEN", "first-tenant-token")
    with tenant_scope(TEST_TENANT_ID):
        assert _Probe({"token_env": "CMDB_TOKEN"}).secret() == "first-tenant-token"
    saved = client.post("/api/organization/connectors", json={
        "name": "cmdb", "type": "http", "config": {"url": "https://cmdb.example.com", "token_env": "CMDB_TOKEN"}})
    assert saved.status_code in (200, 201), saved.text


def test_a_tenants_model_endpoint_key_cannot_be_the_platforms(monkeypatch, northwind_admin):  # noqa: F811
    monkeypatch.setenv("OPENROUTER_API_KEY", "the-platforms-key")
    monkeypatch.setenv("TENANT_NORTHWIND_LLM_API_KEY", "northwind-llm")
    monkeypatch.setenv("TENANT_TEST_LLM_API_KEY", "test-llm")
    policy = {"base_url": "https://llm.northwind.example/v1", "provider_name": "self_hosted"}
    refused = northwind_admin.put("/api/organization/llm-policy", json={**policy, "api_key_env": "OPENROUTER_API_KEY"})
    assert refused.status_code == 400 and "first organization" in refused.json()["detail"]
    saved = northwind_admin.put("/api/organization/llm-policy", json={**policy, "api_key_env": "secret://tenant/llm_api_key"})
    assert saved.status_code == 200, saved.text

    client = LLMClient()
    ref_route = LlmRoute(base_url="https://llm.example/v1", api_key_env="secret://tenant/llm_api_key")
    with tenant_scope(NORTHWIND_ID):
        assert client._client_for(ref_route).api_key == "northwind-llm"
        # A policy stored before US38.10 that names the platform's key is refused when used.
        with pytest.raises(LLMPolicyError, match="first organization"):
            client._client_for(LlmRoute(base_url="https://evil.example/v1", api_key_env="OPENROUTER_API_KEY"))
    with tenant_scope(TEST_TENANT_ID):  # same endpoint, same reference: its own key, not a cached one
        assert client._client_for(ref_route).api_key == "test-llm"


def _migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/d6e7f8a9b0c1_move_secrets_and_files_into_tenants.py"
    spec = importlib.util.spec_from_file_location("move_secrets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_turns_a_tenants_own_variables_into_references():
    m = _migration()
    assert m._as_ref("TENANT_NORTHWIND_OIDC_CLIENT_SECRET", "northwind") == "secret://tenant/oidc_client_secret"
    assert m._as_ref("TENANT_ACME_CO_TOKEN", "acme-co") == "secret://tenant/token"
    assert m._as_ref("TENANT_TEST_OIDC_CLIENT_SECRET", "northwind") is None  # someone else's: left alone
    assert m._as_ref("OIDC_CLIENT_SECRET", "northwind") is None
    # ... and the env backend finds the same variable again.
    assert secret_store.storage_key("acme-co", "token") == "TENANT_ACME_CO_TOKEN"


# ---- files -------------------------------------------------------------------------


def _upload(c, filename: str) -> str:
    process = c.post("/api/processes", json={"name": "Files"}).json()
    response = c.post(f"/api/processes/{process['id']}/documents", files={"files": (filename, b"not a real docx", DOCX)})
    assert response.status_code == 201, response.text
    return process["id"]


def test_uploads_land_in_the_tenants_own_directory(client, northwind_admin):  # noqa: F811
    settings = get_settings()
    ours = _upload(client, "policy.docx")
    theirs = _upload(northwind_admin, "policy.docx")
    root = settings.document_storage_root
    mine = list((root / "tenants" / TEST_TENANT_ID / ours).iterdir())
    assert len(mine) == 1 and mine[0].name.endswith("_policy.docx")
    assert len(list((root / "tenants" / NORTHWIND_ID / theirs).iterdir())) == 1
    assert not (root / ours).exists()  # nothing at the old, shared level
    # Deleting a process takes its files with it, and only its own.
    assert client.delete(f"/api/processes/{ours}").status_code == 204
    assert not (root / "tenants" / TEST_TENANT_ID / ours).exists()
    assert (root / "tenants" / NORTHWIND_ID / theirs).exists()


def test_a_file_name_cannot_climb_out_of_the_tenant(client):
    process_id = _upload(client, "../../tenants/ten_northwind/evil.docx")
    [stored] = (get_settings().document_storage_root / "tenants" / TEST_TENANT_ID / process_id).iterdir()
    assert stored.name.endswith("_evil.docx")
    assert safe_name("..\\..\\x.pdf") == "x.pdf" and safe_name("..") == "unnamed"


def test_without_a_tenant_there_is_no_directory():
    settings = get_settings()
    with tenant_scope(TEST_TENANT_ID):
        assert tenant_dir(settings).name == TEST_TENANT_ID
    from app.tenancy.context import reset_current_tenant, set_current_tenant

    token = set_current_tenant(None)
    try:
        with pytest.raises(TenantFileError):
            tenant_file(settings, "p", "x.pdf")
    finally:
        reset_current_tenant(token)


# ---- logs --------------------------------------------------------------------------


def test_every_log_line_says_which_tenant_it_ran_as():
    def stamp(**extra) -> logging.LogRecord:
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)
        record.__dict__.update(extra)
        RequestIDLogFilter().filter(record)
        return record

    with tenant_scope(NORTHWIND_ID):
        assert stamp().tenant_id == NORTHWIND_ID
        assert stamp(tenant_id="ten_explicit").tenant_id == "ten_explicit"  # e.g. the usage recorder's extra
    from app.tenancy.context import reset_current_tenant, set_current_tenant

    token = set_current_tenant(None)
    try:
        assert stamp().tenant_id == "-"
    finally:
        reset_current_tenant(token)


def test_the_llm_client_still_works_outside_any_tenant_for_scripts(monkeypatch):
    from app.tenancy.context import reset_current_tenant, set_current_tenant

    monkeypatch.setenv("SELF_HOSTED_KEY", "script-key")
    token = set_current_tenant(None)
    try:
        route = LlmRoute(base_url="https://llm.example/v1", api_key_env="SELF_HOSTED_KEY")
        assert LLMClient()._client_for(route).api_key == "script-key"
    finally:
        reset_current_tenant(token)
