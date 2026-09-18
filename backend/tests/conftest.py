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
# A dedicated test database, not the dev one -- see docker-compose.yml. Must
# already exist with migrations applied (`alembic upgrade head` against
# this URL); tests truncate its tables between runs but don't create it.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://agentic:agentic@127.0.0.1:5432/agentic_solution_generator_test"
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TEST_UPLOAD_DIR = Path(__file__).resolve().parents[1] / ".data" / "test-uploads"
shutil.rmtree(_TEST_UPLOAD_DIR, ignore_errors=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.models import Base  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.llm.client import get_llm_client  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    """Every test starts with empty Epic-2 tables. Truncation (not a
    wrapping transaction that gets rolled back) because
    app/ingestion/pipeline.py deliberately opens its own DB session/
    connection for background tasks -- a wrapping-transaction approach
    would leave that session unable to see fixture-created rows."""
    table_names = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    yield


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
        yield test_client
    app.dependency_overrides.pop(get_llm_client, None)
