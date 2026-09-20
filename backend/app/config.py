from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved relative to this file (not the process CWD) so `.env` loads
# correctly whether the app/scripts/tests are run from backend/ or elsewhere.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class RegistryConfig(BaseModel):
    """Epic 13, US13.3/US13.4: one configured registry *instance* -- name
    is the connector's identity (what push/search target by), type picks
    the connector implementation (app/registry/manager.py). Naming them
    separately (rather than using type as the identity) is what lets two
    registries of the same type coexist under different names, e.g. two
    "local" registries for two teams, without a second real connector
    type having to exist first."""

    name: str
    type: str


class Settings(BaseSettings):
    """Backend configuration, loaded from environment variables / .env.

    Instantiating this with OPENROUTER_API_KEY unset raises a pydantic
    ValidationError immediately -- this is the "fail fast" behavior called
    for in the local-stack-bootstrap skill, rather than a working service
    that fails on the first LLM call.
    """

    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str
    llm_model: str = "qwen/qwen3.7-flash"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_site_url: str | None = None
    llm_app_name: str = "Agentic Solution Generator"
    llm_request_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=1)

    llm_usage_log_enabled: bool = True
    llm_usage_log_path: str = ".data/llm_usage.jsonl"

    # Epic 10 (cross-cutting logging): a persistent app log file alongside
    # the console output logging.basicConfig already gave us -- distinct
    # from llm_usage_log_path above, which is a structured cost/usage
    # ledger, not general request/error logging.
    app_log_enabled: bool = True
    app_log_path: str = ".data/app.log"

    document_storage_path: str = ".data/uploads"

    # Matches docker-compose.yml's `db` service (Epic 2, US2.2/US2.3 -- pgvector
    # lives on this same Postgres instance, see local-stack-bootstrap skill).
    database_url: str = "postgresql+psycopg://agentic:agentic@127.0.0.1:5432/agentic_solution_generator"
    embedding_model_name: str = "all-MiniLM-L6-v2"

    # Epic 13: which registries are connected. A JSON list, e.g.
    # '[{"name": "internal", "type": "local"}, {"name": "vendor-x", "type": "local"}]'
    # -- only "local" (app/registry/local.py) is implemented today; the
    # target external registry product/standard is deliberately undecided
    # (see planning/backlog.md), so this defaults to a single local one.
    registries: list[RegistryConfig] = Field(default_factory=lambda: [RegistryConfig(name="local", type="local")])

    @property
    def usage_log_path(self) -> Path:
        path = Path(self.llm_usage_log_path)
        return path if path.is_absolute() else _ENV_FILE.parent / path

    @property
    def app_log_file_path(self) -> Path:
        path = Path(self.app_log_path)
        return path if path.is_absolute() else _ENV_FILE.parent / path

    @property
    def document_storage_root(self) -> Path:
        path = Path(self.document_storage_path)
        return path if path.is_absolute() else _ENV_FILE.parent / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
