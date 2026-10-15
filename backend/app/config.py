from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
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

    # Epic 14 (core slice): bounds a twin run's tool-calling loop so a
    # scenario that never produces a final answer fails fast (status
    # "error") instead of looping indefinitely against a live LLM.
    twin_max_loop_turns: int = Field(default=8, ge=1)

    # Epic 16: bounds an orchestration rehearsal's total node-visit count so
    # a rework loop with no configured exit gateway decision fails fast
    # (status "error") instead of looping indefinitely against a live LLM.
    orchestration_max_total_steps: int = Field(default=50, ge=1)

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
    # Epic 28, US28.8: re-run the regression suites of agents whose pinned
    # Context Pack contained an entity that just changed.
    context_auto_rerun_suites: bool = True
    # Epic 29, US29.5: extra hosts (fnmatch patterns, comma separated) a
    # twin run may call in API mode, on top of the built-in non-production
    # rules in app/catalog/endpoints.py.
    twin_api_allowed_hosts: str = ""
    twin_api_timeout_seconds: float = Field(default=20.0, gt=0)
    # Epic 31, US31.1: the durable job queue (app/jobs/). The worker runs in
    # the API process unless disabled (tests disable it and drain the queue
    # explicitly); run `python -m app.jobs.worker` for a separate worker.
    job_worker_enabled: bool = True
    job_poll_seconds: float = Field(default=2.0, gt=0)
    job_concurrency: int = Field(default=2, ge=1)
    job_lease_seconds: float = Field(default=900.0, gt=0)
    job_backoff_seconds: float = Field(default=5.0, ge=0)
    connector_timeout_seconds: float = Field(default=30.0, gt=0)
    # Epic 31, US31.8: what the default LLM provider is and where it runs,
    # for business-unit residency checks. Empty region = unknown, which
    # satisfies no residency requirement.
    # Epic 34, US34.4/US34.5: at most this many concurrent requests per
    # provider endpoint + model from this process (batches would otherwise
    # trip provider rate limits en masse), and cache-mark the stable prompt
    # prefix where the provider supports explicit marking.
    llm_max_concurrency: int = Field(default=8, ge=1)
    llm_prompt_cache: bool = True
    llm_provider_name: str = "openrouter"
    llm_provider_region: str = ""

    # Epic 13: which registries are connected. A JSON list, e.g.
    # '[{"name": "internal", "type": "local"}, {"name": "vendor-x", "type": "local"}]'
    # -- only "local" (app/registry/local.py) is implemented today; the
    # target external registry product/standard is deliberately undecided
    # (see planning/backlog.md), so this defaults to a single local one.
    registries: list[RegistryConfig] = Field(default_factory=lambda: [RegistryConfig(name="local", type="local")])

    # Epic 37, US37.14: where this deployment runs and how people sign in.
    # `local` is the name-and-role picker -- anyone can be anyone -- so it
    # is refused outright in production (see _check_auth_safety). The
    # backend Dockerfile sets ENVIRONMENT=production, so an image deployed
    # without auth configuration fails closed.
    environment: Literal["development", "test", "demo", "production"] = "development"
    auth_mode: Literal["local", "oidc", "saml"] = "local"
    # US37.8: only takes effect once SSO lands; refused as false in production.
    auth_require_mfa: bool = True
    # US37.12: a sign-in lasts `auth_session_max_hours` at most, and ends
    # sooner after `auth_idle_timeout_minutes` without a request. The
    # session cookie itself is good for `auth_access_token_minutes`; the
    # browser then swaps its refresh cookie for a new pair.
    auth_access_token_minutes: int = Field(default=15, ge=1)
    auth_idle_timeout_minutes: int = Field(default=30, ge=1)
    auth_session_max_hours: int = Field(default=12, ge=1)
    # Unset = Secure cookies in production only. Python clients (the demo
    # seed, TestClient) don't send Secure cookies over plain http, even
    # to localhost, which is what development, test and demo run on.
    auth_cookie_secure: bool | None = None

    @property
    def cookie_secure(self) -> bool:
        return self.environment == "production" if self.auth_cookie_secure is None else self.auth_cookie_secure

    @model_validator(mode="after")
    def _check_auth_safety(self) -> "Settings":
        if self.environment == "production" and self.auth_cookie_secure is False:
            raise ValueError(
                "AUTH_COOKIE_SECURE=false is not allowed with ENVIRONMENT=production: session cookies would be "
                "sent over plain http. Serve the API over https and leave AUTH_COOKIE_SECURE unset."
            )
        if self.environment == "production" and self.auth_mode == "local":
            raise ValueError(
                "AUTH_MODE=local is not allowed with ENVIRONMENT=production: local sign-in lets anyone sign in "
                "as anyone with any role. Configure single sign-on (AUTH_MODE=oidc or saml), or set ENVIRONMENT "
                "to development, test or demo if this is not a production deployment."
            )
        if self.auth_mode in ("oidc", "saml"):
            # Epic 37 build order: SSO (US37.7-37.9) isn't built yet. Refusing
            # here beats starting up with a sign-in page that can't work.
            raise ValueError(
                f"AUTH_MODE={self.auth_mode} is not available in this build yet -- use AUTH_MODE=local outside production"
            )
        if self.environment == "production" and not self.auth_require_mfa:
            raise ValueError("AUTH_REQUIRE_MFA=false is not allowed with ENVIRONMENT=production")
        return self

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


def settings_or_exit(component: str) -> Settings:
    """Epic 37, US37.14: fail loudly, before binding a port or polling a
    queue, when the settings are unsafe. Prints only the reasons: pydantic's
    own message echoes the input, which includes secrets such as the
    database URL."""
    try:
        return get_settings()
    except ValidationError as exc:
        reasons = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
        raise SystemExit(f"Refusing to start the {component}: {reasons}") from None
