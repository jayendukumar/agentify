import os
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock

# Ensure OPENROUTER_API_KEY is set before app.config.Settings is imported anywhere,
# so pydantic-settings validation doesn't fail during test collection.
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-v1-test-key")
os.environ.setdefault("LLM_USAGE_LOG_ENABLED", "false")
os.environ.setdefault("DOCUMENT_STORAGE_PATH", ".data/test-uploads")
# Epic 31: tests drain the job queue explicitly (app.jobs.worker.run_pending)
# rather than racing a background worker.
os.environ.setdefault("JOB_WORKER_ENABLED", "false")
# Epic 37, US37.14: local sign-in is refused in production.
os.environ.setdefault("ENVIRONMENT", "test")
# A dedicated test database, not the dev one -- see docker-compose.yml. Must
# already exist with migrations applied (`alembic upgrade head` against
# this URL); tests truncate its tables between runs but don't create it.
# Epic 38: the app connects as the app role, so row-level security applies
# exactly as in production; truncation and tenant setup use the owner.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://axyntro_app:axyntro_app@127.0.0.1:5432/agentic_solution_generator_test"
)
# Epic 38, US38.6: the test tenant is the first tenant, so the OIDC_*
# environment variables tests set are its sign-in defaults.
os.environ.setdefault("TENANT_DEFAULT_SLUG", "test")
os.environ.setdefault(
    "DATABASE_OWNER_URL", "postgresql+psycopg://agentic:agentic@127.0.0.1:5432/agentic_solution_generator_test"
)
# Epic 38, US38.8: the operator console's role, on the same test database
# and port as DATABASE_URL.
os.environ.setdefault(
    "DATABASE_PLATFORM_URL",
    os.environ["DATABASE_URL"].replace("axyntro_app:axyntro_app@", "axyntro_platform:axyntro_platform@"),
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TEST_UPLOAD_DIR = Path(__file__).resolve().parents[1] / ".data" / "test-uploads"
shutil.rmtree(_TEST_UPLOAD_DIR, ignore_errors=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.models import Base  # noqa: E402
from app.db.session import get_owner_engine, isolation_enforced  # noqa: E402
from app.llm import keys as llm_keys  # noqa: E402
from app.llm.client import get_llm_client  # noqa: E402
from app.main import app  # noqa: E402
from app.tenancy.context import reset_current_tenant, set_current_tenant  # noqa: E402
from app.tenancy.limits import forget_cached_limits  # noqa: E402

ALL_ROLES = ["analyst", "architect", "process_owner", "approver", "auditor", "admin"]

# Epic 38: every test runs in this tenant, reached at TestClient's default
# host. Tests about isolation add more with `make_tenant`.
TEST_TENANT_ID = "ten_test"
TEST_HOST = "testserver"


def make_tenant(tenant_id: str, slug: str, hosts: list[str], status: str = "active") -> str:
    """Creates a tenant as the platform operator would (the app role can't
    write platform tables)."""
    with get_owner_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO tenants (id, slug, display_name, status) VALUES (:id, :slug, :name, :status)"),
            {"id": tenant_id, "slug": slug, "name": slug.title(), "status": status},
        )
        for host in hosts:
            conn.execute(text("INSERT INTO tenant_domains (host, tenant_id) VALUES (:h, :t)"), {"h": host, "t": tenant_id})
    return tenant_id


def pytest_sessionstart(session):
    # A superuser test connection would skip row-level security and every
    # isolation test would pass for the wrong reason.
    if not isolation_enforced():
        raise pytest.UsageError(
            "DATABASE_URL connects as a superuser or BYPASSRLS role; tests must use the app role "
            "(axyntro_app), with the owner in DATABASE_OWNER_URL. See tests/conftest.py."
        )


def sign_in(test_client: TestClient, name: str, roles: list[str], business_unit_id: str | None = None):
    """Local sign-in, keeping the CSRF token every write needs (Epic 37,
    US37.12) as a default header -- what the frontend's API client does."""
    body: dict = {"name": name, "roles": roles}
    if business_unit_id:
        body["business_unit_id"] = business_unit_id
    response = test_client.post("/api/auth/login", json=body)
    if response.status_code == 200:
        test_client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


@pytest.fixture(autouse=True)
def _clean_db():
    """Every test starts with empty Epic-2 tables. Truncation (not a
    wrapping transaction that gets rolled back) because
    app/ingestion/pipeline.py deliberately opens its own DB session/
    connection for background tasks -- a wrapping-transaction approach
    would leave that session unable to see fixture-created rows."""
    table_names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    with get_owner_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    make_tenant(TEST_TENANT_ID, "test", [TEST_HOST])
    # Epic 38: per-process caches of tenant settings, emptied with the tables.
    forget_cached_limits()
    llm_keys.forget()
    # Code a test calls directly (repositories, background tasks, the job
    # worker's run_pending) runs as the test tenant; requests resolve their
    # own tenant from the host, as in production.
    token = set_current_tenant(TEST_TENANT_ID)
    yield
    reset_current_tenant(token)


class FakeLLMClient:
    """Stands in for LLMClient in tests -- `complete` is an AsyncMock the
    test configures per-case, so no real network call is ever made."""

    def __init__(self) -> None:
        self.complete = AsyncMock()


@pytest.fixture
def fake_llm():
    return FakeLLMClient()


@pytest.fixture
def client(fake_llm):
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as test_client:
        # Epic 9/10, US9.9: every route requires a logged-in user. Logging in
        # once, holding every Epic 37 role, keeps pre-existing tests about
        # what they test -- TestClient persists cookies like a browser.
        # Segregation of duties still applies: this user can't approve what
        # it changed, which is what `approver_client` is for. Tests of
        # access control itself use a fresh TestClient (test_api_auth.py).
        sign_in(test_client, "test-editor", ALL_ROLES)
        yield test_client
    app.dependency_overrides.pop(get_llm_client, None)


@pytest.fixture
def viewer_client(fake_llm):
    """A second logged-in client, as a viewer -- for tests asserting
    access-control (403 on write endpoints), reusing the client fixture's
    login-once pattern rather than each test wiring this up itself."""
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as test_client:
        sign_in(test_client, "test-viewer", [])
        yield test_client
    app.dependency_overrides.pop(get_llm_client, None)


@pytest.fixture
def approver_client(fake_llm):
    """Epic 37: a second person who approves what `client` changed -- a
    process owner for baselines and an approver for agent releases."""
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    with TestClient(app) as test_client:
        sign_in(test_client, "test-approver", ["process_owner", "approver"])
        yield test_client
    app.dependency_overrides.pop(get_llm_client, None)


def approve_latest_version(approver, process_id: str) -> dict:
    """Approves the process's most recent version awaiting approval."""
    versions = approver.get(f"/api/processes/{process_id}/versions").json()
    pending = [v for v in versions if v["status"] == "pending_approval"]
    assert pending, f"no version awaiting approval: {versions}"
    response = approver.post(f"/api/processes/{process_id}/versions/{pending[-1]['id']}/approve", json={})
    assert response.status_code == 200, response.text
    return response.json()
