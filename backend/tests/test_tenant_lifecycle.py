"""Epic 38, US38.13: suspend, export and delete.

Suspending keeps everything and lets nobody in; an admin's export holds
every tenant table, the files and the audit log, and downloads once;
deleting waits, then erases every tenant row, file and secret, and leaves
a certificate in the platform log -- and nothing of the tenant's data."""

import asyncio
import io
import json
import shutil
import zipfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app import secret_store
from app.config import get_settings
from app.db.models import PlatformAuditEventModel, TenantDomainModel, TenantExportModel, TenantModel
from app.db.session import get_owner_engine, get_platform_session_factory, get_session_factory
from app.ids import utcnow
from app.jobs.worker import run_pending
from app.main import app
from app.tenancy import tenant_scope
from app.tenancy.data import EXPORT_REDACTED_COLUMNS, tenant_tables
from app.tenancy.export import archive_path

from .conftest import TEST_TENANT_ID, sign_in
from .test_tenant_identity import NORTHWIND, NORTHWIND_ID, northwind_admin  # noqa: F401
from .test_tenant_secrets_files import _upload

OPS = "http://ops.localhost"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("OPERATOR_AUTH_MODE", "local")
    get_settings.cache_clear()
    secret_store.get_store.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()
    secret_store.get_store.cache_clear()


def _no_wait(monkeypatch):
    monkeypatch.setenv("TENANT_DELETION_WAIT_DAYS", "0")
    get_settings.cache_clear()


def _operator() -> TestClient:
    ops = TestClient(app, base_url=OPS)
    response = ops.post("/api/operator/login", json={"name": "Otto Operator", "email": "otto@hosting.test"})
    ops.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return ops


def _tenant_row(tenant_id: str) -> TenantModel | None:
    with get_platform_session_factory()() as db:
        return db.get(TenantModel, tenant_id)


def _rows(tenant_id: str) -> int:
    with tenant_scope(tenant_id), get_session_factory()() as db:
        return sum(db.scalar(select(func.count()).select_from(t)) for t in tenant_tables())


def _export(c: TestClient) -> dict:
    requested = c.post("/api/organization/exports")
    assert requested.status_code == 202, requested.text
    assert asyncio.run(run_pending(kinds=["tenant_export"])) == 1
    [done] = [e for e in c.get("/api/organization/exports").json() if e["id"] == requested.json()["id"]]
    return done


# ---- suspend -------------------------------------------------------------------------


def test_a_suspended_tenant_lets_nobody_in_runs_nothing_and_keeps_everything(northwind_admin):  # noqa: F811
    northwind_admin.post("/api/processes", json={"name": "Northwind invoicing"})
    ops = _operator()
    suspended = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/suspend")
    assert suspended.status_code == 200 and suspended.json()["suspended_at"] is not None

    # Sign-ins, the signed-in session, and SCIM are refused alike.
    assert sign_in(TestClient(app, base_url=f"http://{NORTHWIND}"), "Nora Admin", []).status_code == 403
    assert northwind_admin.get("/api/processes").status_code == 403
    scim = TestClient(app, base_url=f"http://{NORTHWIND}").get("/scim/v2/Users", headers={"Authorization": "Bearer x"})
    assert scim.status_code == 403
    # Its jobs wait.
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        db.execute(text("INSERT INTO jobs (id, kind, payload, status, priority, attempts, max_attempts) "
                        "VALUES ('job_paused', 'tenant_export', '{}', 'queued', 100, 0, 1)"))
        db.commit()
    asyncio.run(run_pending())
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        assert db.scalar(text("SELECT status FROM jobs WHERE id = 'job_paused'")) == "queued"

    # Nothing was removed: back on, the same data.
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        db.execute(text("DELETE FROM jobs WHERE id = 'job_paused'"))
        db.commit()
    activated = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/activate").json()
    assert activated["status"] == "active" and activated["suspended_at"] is None
    assert [p["name"] for p in northwind_admin.get("/api/processes").json()] == ["Northwind invoicing"]


# ---- export --------------------------------------------------------------------------


def test_an_admins_export_holds_every_table_the_files_and_the_audit_log(client, northwind_admin):  # noqa: F811
    # The test tenant's directory outlives the rows other tests truncated.
    shutil.rmtree(get_settings().document_storage_root / "tenants" / TEST_TENANT_ID, ignore_errors=True)
    process_id = _upload(client, "policy.docx")
    northwind_admin.post("/api/processes", json={"name": "Northwind invoicing"})
    done = _export(client)
    assert done["status"] == "ready" and done["rows"] > 0 and done["file_count"] == 1, done

    response = client.get(f"/api/organization/exports/{done['id']}/download")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["tenant_id"] == TEST_TENANT_ID and manifest["files"] == 1
    # Every tenant table but sign-ins in flight and one-time links.
    expected = {t.name for t in tenant_tables()} - {"session_tokens", "oidc_login_requests", "support_entries"}
    assert {n.removeprefix("tables/").removesuffix(".json") for n in names if n.startswith("tables/")} == expected
    processes = json.loads(archive.read("tables/processes.json"))
    assert [p["id"] for p in processes] == [process_id]
    assert all(p["tenant_id"] == TEST_TENANT_ID for p in processes)
    assert "Northwind" not in archive.read("tables/processes.json").decode()
    audit = json.loads(archive.read("tables/audit_events.json"))
    assert any(e["action"] == "tenant_export_requested" for e in audit)
    assert [n for n in names if n.startswith("files/")][0].startswith(f"files/{process_id}/")
    # Credential hashes stay out.
    sessions = json.loads(archive.read("tables/sessions.json"))
    assert sessions and all(column not in sessions[0] for table, column in EXPORT_REDACTED_COLUMNS
                            if table == "sessions")
    assert "token_hash" not in archive.read("tables/scim_tokens.json").decode()

    # Once: the second download is refused, and the file is gone.
    assert client.get(f"/api/organization/exports/{done['id']}/download").status_code == 410
    with tenant_scope(TEST_TENANT_ID):
        assert not archive_path(done["id"]).exists()
    listed = client.get("/api/organization/exports").json()[0]
    assert listed["status"] == "downloaded" and listed["downloaded_by_name"] == "test-editor"
    # Audited, and the next export doesn't contain the last one.
    second = _export(client)
    with zipfile.ZipFile(archive_path(second["id"])) as again:
        assert not [n for n in again.namelist() if n.startswith("files/_exports")]
        assert any(e["action"] == "tenant_export_downloaded"
                   for e in json.loads(again.read("tables/audit_events.json")))


def test_only_admins_export_one_at_a_time_and_unclaimed_archives_expire(client, viewer_client):
    assert viewer_client.post("/api/organization/exports").status_code == 403
    assert client.post("/api/organization/exports").status_code == 202
    assert client.post("/api/organization/exports").status_code == 409
    asyncio.run(run_pending(kinds=["tenant_export"]))
    [ready] = client.get("/api/organization/exports").json()
    assert ready["status"] == "ready" and ready["expires_at"] is not None
    # Another tenant's admin can't fetch it either.
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as db:
        db.execute(update(TenantExportModel).values(
            finished_at=utcnow().replace(tzinfo=None) - timedelta(hours=get_settings().tenant_export_retention_hours + 1)))
        db.commit()
    assert client.get("/api/organization/exports").json()[0]["status"] == "expired"
    with tenant_scope(TEST_TENANT_ID):
        assert not archive_path(ready["id"]).exists()
    assert client.get(f"/api/organization/exports/{ready['id']}/download").status_code == 410


def test_another_tenants_admin_cannot_download_an_export(client, northwind_admin):  # noqa: F811
    done = _export(client)
    assert northwind_admin.get(f"/api/organization/exports/{done['id']}/download").status_code == 410
    assert client.get(f"/api/organization/exports/{done['id']}/download").status_code == 200


# ---- delete --------------------------------------------------------------------------


def test_deleting_waits_and_can_be_undone(northwind_admin):  # noqa: F811
    ops = _operator()
    assert ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/delete").status_code == 409  # suspend first
    ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/suspend")
    deleting = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/delete").json()
    assert deleting["status"] == "deleting" and deleting["delete_after"] is not None
    refused = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/erase", json={"confirm_slug": "northwind"})
    assert refused.status_code == 409 and "waiting period" in refused.json()["detail"]
    restored = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/restore").json()
    assert restored["status"] == "suspended" and restored["delete_after"] is None
    assert _rows(NORTHWIND_ID) > 0


def test_erasing_removes_every_row_file_and_secret_and_keeps_a_certificate(
    client, northwind_admin, monkeypatch, tmp_path  # noqa: F811
):
    _no_wait(monkeypatch)
    monkeypatch.setenv("SECRETS_BACKEND", "files")
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    get_settings.cache_clear()
    secret_store.get_store.cache_clear()
    (tmp_path / "TENANT_NORTHWIND_OIDC_CLIENT_SECRET").write_text("nw-secret")
    (tmp_path / "TENANT_TEST_OIDC_CLIENT_SECRET").write_text("ours")
    ours = _upload(client, "policy.docx")
    theirs = _upload(northwind_admin, "contract.docx")
    northwind_admin.post("/api/processes", json={"name": "Northwind invoicing"})
    before_ours = _rows(TEST_TENANT_ID)
    root = get_settings().document_storage_root / "tenants"

    ops = _operator()
    # The platform log named the tenant's first admin when it was created.
    with get_platform_session_factory()() as db:
        db.add(PlatformAuditEventModel(id="paudit_created", operator_name="Otto Operator", action="tenant_created",
                                       tenant_id=NORTHWIND_ID, detail={"first_admin": {"email": "nora@northwind.test"}}))
        db.commit()
    ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/suspend")
    ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/delete")
    wrong = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/erase", json={"confirm_slug": "test"})
    assert wrong.status_code == 400
    erased = ops.post(f"/api/operator/tenants/{NORTHWIND_ID}/erase", json={"confirm_slug": "northwind"})
    assert erased.status_code == 200, erased.text
    cert = erased.json()["certificate"]

    # Every tenant row, as the tenant: none left.
    assert _rows(NORTHWIND_ID) == 0
    assert cert["rows_total"] > 0 and cert["tables"]["processes"] == 2 and cert["tables"]["users"] == 1
    assert set(cert["tables"]) == {t.name for t in tenant_tables()} and cert["verified_empty"]
    # Its files and secrets.
    assert not (root / NORTHWIND_ID).exists() and cert["files_removed"] == 1
    assert not (tmp_path / "TENANT_NORTHWIND_OIDC_CLIENT_SECRET").exists()
    assert cert["secrets"]["removed"] == ["TENANT_NORTHWIND_OIDC_CLIENT_SECRET"]
    assert "encryption key" in cert["encryption_key"] or cert["encryption_key"].startswith("none")
    # The platform rows are gone, so the slug and host are free again.
    assert _tenant_row(NORTHWIND_ID) is None
    with get_platform_session_factory()() as db:
        assert db.get(TenantDomainModel, NORTHWIND) is None
        events = list(db.scalars(select(PlatformAuditEventModel).where(PlatformAuditEventModel.tenant_id == NORTHWIND_ID)))
    # The certificate is kept; nothing of the tenant's data is.
    assert "nora@northwind.test" not in json.dumps([e.detail for e in events])
    assert "Northwind invoicing" not in json.dumps([e.detail for e in events])
    [listed] = ops.get("/api/operator/deletion-certificates").json()
    assert listed["certificate"]["tenant_id"] == NORTHWIND_ID
    # Nobody else lost anything.
    assert _rows(TEST_TENANT_ID) == before_ours
    assert (root / TEST_TENANT_ID / ours).exists() and (tmp_path / "TENANT_TEST_OIDC_CLIENT_SECRET").exists()
    assert theirs not in client.get("/api/processes").text
    assert ops.post("/api/operator/tenants", json={"slug": "northwind", "host": NORTHWIND,
                                                   "first_admin_email": "new@northwind.test"}).status_code == 201


def test_a_secret_that_might_be_another_tenants_is_kept(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRETS_BACKEND", "files")
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    get_settings.cache_clear()
    for name in ("TENANT_ACME_TOKEN", "TENANT_ACME_EU_TOKEN", "TENANT_ACMEX_TOKEN"):
        (tmp_path / name).write_text("x")
    result = secret_store.erase("acme", other_slugs=["acme-eu", "acmex"])
    assert result["removed"] == ["TENANT_ACME_TOKEN"] and result["ambiguous_kept"] == ["TENANT_ACME_EU_TOKEN"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TENANT_ACMEX_TOKEN", "TENANT_ACME_EU_TOKEN"]


def test_with_the_env_backend_the_operator_is_told_what_to_remove(monkeypatch):
    monkeypatch.setenv("TENANT_GONE_DB_PASSWORD", "x")
    result = secret_store.erase("gone", other_slugs=[])
    assert result["remove_from_deployment_environment"] == ["TENANT_GONE_DB_PASSWORD"]
    assert secret_store.resolve("secret://tenant/db_password", "gone") is None


def test_the_console_role_still_has_no_privilege_on_tenant_tables():
    with get_owner_engine().connect() as conn:
        granted = conn.execute(text(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'axyntro_platform' AND table_name = ANY(:tables)"),
            {"tables": [t.name for t in tenant_tables()]}).all()
    assert granted == []
