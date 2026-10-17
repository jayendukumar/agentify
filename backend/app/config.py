import os
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
    #
    # Epic 38, US38.2: the API and worker connect as `axyntro_app`, a role
    # without BYPASSRLS that owns no tables, so row-level security keeps
    # tenants apart. Migrations (and `python -m app.tenancy`) connect as the
    # tables' owner, DATABASE_OWNER_URL; the first migration of Epic 38
    # creates the app role (password DATABASE_APP_PASSWORD).
    database_url: str = "postgresql+psycopg://axyntro_app:axyntro_app@127.0.0.1:5432/agentic_solution_generator"
    database_owner_url: str | None = "postgresql+psycopg://agentic:agentic@127.0.0.1:5432/agentic_solution_generator"
    embedding_model_name: str = "all-MiniLM-L6-v2"
    # Epic 28, US28.8: re-run the regression suites of agents whose pinned
    # Context Pack contained an entity that just changed.
    context_auto_rerun_suites: bool = True
    # Epic 29, US29.5: extra hosts (fnmatch patterns, comma separated) a
    # twin run may call in API mode, on top of the built-in non-production
    # rules in app/catalog/endpoints.py.
    twin_api_allowed_hosts: str = ""
    twin_api_timeout_seconds: float = Field(default=20.0, gt=0)
    # Epic 39: the agent runtime. Production adapters call only https
    # endpoints, except these hosts (comma separated fnmatch patterns),
    # meant for a local stand-in of a production system.
    runtime_http_hosts: str = ""
    # Hosts a run's completion callback may be sent to (comma separated
    # fnmatch patterns). Empty: callbacks are refused, so a caller can't
    # make the worker call an arbitrary address.
    runtime_callback_hosts: str = ""
    runtime_max_turns: int = Field(default=8, ge=1)
    # US39.4: the audience an OAuth runtime client's access token must
    # carry, unless the client is registered with its own (an Entra ID
    # tenant names it after its app registration, api://...).
    runtime_oauth_audience: str = "axyntro-runtime"
    # US39.18: the Ed25519 key that signs a hand-off's evidence (PEM). Made
    # on first use outside production; production must provide it.
    handoff_signing_key_file: str = ".data/handoff-signing-key.pem"
    # Job kinds this process's worker claims ("all", or comma separated):
    # `runtime_execution` alone gives production its own workers (US39.3).
    job_worker_kinds: str = "all"
    # Epic 31, US31.1: the durable job queue (app/jobs/). The worker runs in
    # the API process unless disabled (tests disable it and drain the queue
    # explicitly); run `python -m app.jobs.worker` for a separate worker.
    job_worker_enabled: bool = True
    job_poll_seconds: float = Field(default=2.0, gt=0)
    job_concurrency: int = Field(default=2, ge=1)
    job_lease_seconds: float = Field(default=900.0, gt=0)
    job_backoff_seconds: float = Field(default=5.0, ge=0)
    # Epic 38, US38.9: each tenant's limits, unless an operator sets its
    # own on the tenant (app/tenancy/limits.py). Unset means no limit.
    # Concurrent jobs count across workers; requests per minute are
    # counted per process.
    tenant_max_concurrent_jobs: int | None = Field(default=None, ge=1)
    tenant_llm_requests_per_minute: int | None = Field(default=None, ge=1)
    tenant_llm_monthly_budget_usd: float | None = Field(default=None, gt=0)
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
    # US37.8: SSO sign-ins must show MFA was used; refused as false in production.
    auth_require_mfa: bool = True
    # What counts as MFA: any of these `amr` values in the ID token, or an
    # `acr` in auth_mfa_acr_values (e.g. a Keycloak level of authentication
    # mapped to "mfa").
    auth_mfa_amr_values: list[str] = Field(default_factory=lambda: ["mfa", "otp", "hwk", "swk"])
    auth_mfa_acr_values: list[str] = Field(default_factory=list)

    # US37.7: OpenID Connect sign-in (AUTH_MODE=oidc). The API runs the
    # whole exchange; the browser only ever sees our own session cookies.
    # `oidc_issuer` must equal the ID token's `iss` exactly.
    oidc_issuer: str = ""
    # Where the API fetches the discovery document. Defaults to the
    # issuer's; set it when the API reaches the IdP by another address
    # than browsers do (Keycloak in docker compose: http://keycloak:8080).
    oidc_discovery_url: str = ""
    oidc_client_id: str = ""
    # The *name* of the environment variable holding the first tenant's
    # client secret. Other tenants reference theirs as secret://tenant/<name>
    # (Epic 38, US38.10, app/secret_store).
    oidc_client_secret_env: str = "OIDC_CLIENT_SECRET"
    oidc_redirect_uri: str = "http://localhost:8000/api/auth/oidc/callback"
    oidc_scopes: str = "openid profile email"
    # Shown on the sign-in button: "Sign in with <oidc_display_name>".
    oidc_display_name: str = "single sign-on"
    # Sent as `acr_values` on every sign-in, to ask the IdP for MFA.
    oidc_acr_values: str = ""
    oidc_groups_claim: str = "groups"
    # Create the user at first sign-in. Off: only users already known (by
    # issuer + subject) may sign in.
    oidc_jit_provisioning: bool = True
    # US37.11: the first group-to-role mappings, copied into the table
    # (app/auth/directory.py) the first time mappings are needed, so a new
    # deployment's first admin can sign in. After that, admins edit them on
    # the Directory page. JSON: group -> roles, each "role" or
    # "role@<business unit name>", e.g.
    # '{"axyntro-admins": ["admin"], "retail-analysts": ["analyst@Retail Banking"]}'.
    oidc_group_roles: dict[str, list[str]] = Field(default_factory=dict)
    # Where the browser lands after signing in or out.
    frontend_url: str = "http://localhost:3000"
    # The API's address as the IdP reaches it -- shown to admins as the
    # SCIM base URL (US37.10): <api_public_url>/scim/v2.
    api_public_url: str = "http://localhost:8000"
    # US37.10: the ID token claim whose value is a SCIM user's externalId.
    # It links a provisioned user to their first SSO sign-in. Keycloak and
    # Okta: "sub" (provision externalId = the user id). Entra ID: "oid"
    # (provision externalId = objectId).
    scim_link_claim: str = "sub"

    # Epic 38, US38.1/US38.4: each tenant is reached at its own host names
    # (table tenant_domains). Upgrading an existing deployment puts its data
    # in one tenant, with this slug and display name, reached at these hosts
    # plus the hosts of FRONTEND_URL, API_PUBLIC_URL and OIDC_REDIRECT_URI.
    # Only read by that migration; afterwards `python -m app.tenancy` manages
    # tenants and hosts.
    tenant_default_slug: str = "default"
    # Epic 38, US38.10: where `secret://tenant/<name>` references are kept
    # (app/secret_store): `env` -- TENANT_<SLUG>_<NAME> environment variables --
    # or `files`, one file per secret, so named, in `secrets_dir`.
    secrets_backend: Literal["env", "files"] = "env"
    secrets_dir: str = "/run/secrets"
    tenant_default_name: str | None = None
    tenant_default_hosts: str = "localhost,127.0.0.1"
    # Browsers call the API from the tenant's own host (frontend/src/api/
    # client.ts), so locally any <tenant>.localhost:3000 is an allowed origin.
    cors_origin_regex: str | None = r"^http://([a-z0-9-]+\.)?localhost:3000$"

    # Epic 38, US38.8: the operator console. It answers only on these hosts,
    # which reach no tenant, and connects to the database as its own role,
    # which may write the platform tables and can't read any tenant table.
    # Operators sign in through the platform's own IdP, with MFA, and must
    # be in `operator_group`. `local` (a name, no password) is for
    # development and refused in production, like AUTH_MODE=local; `none`
    # (the default) keeps the console closed until it's configured.
    operator_hosts: str = "ops.localhost"
    database_platform_url: str | None = (
        "postgresql+psycopg://axyntro_platform:axyntro_platform@127.0.0.1:5432/agentic_solution_generator"
    )
    operator_auth_mode: Literal["local", "oidc", "none"] = "none"
    operator_oidc_issuer: str = ""
    operator_oidc_discovery_url: str = ""
    operator_oidc_client_id: str = ""
    operator_oidc_client_secret_env: str = "OPERATOR_OIDC_CLIENT_SECRET"
    operator_oidc_acr_values: str = ""
    operator_mfa_amr_values: list[str] = Field(default_factory=lambda: ["mfa", "otp", "hwk", "swk"])
    operator_mfa_acr_values: list[str] = Field(default_factory=list)
    operator_group: str = "platform-operators"
    operator_session_hours: int = Field(default=8, ge=1)
    # The longest support grant a tenant admin can give.
    support_max_hours: int = Field(default=72, ge=1)
    # Epic 38, US38.13: a deleted tenant's data is erased no sooner than
    # this many days after the operator deletes it (it must be suspended
    # first), so a mistake can still be undone. A data export the admin
    # hasn't downloaded is removed after `tenant_export_retention_hours`.
    tenant_deletion_wait_days: float = Field(default=30, ge=0)
    tenant_export_retention_hours: float = Field(default=72, gt=0)
    # US38.15: the region this deployment serves (one deployment per
    # region). Empty: no residency rules. When set, tenants are created
    # only in this region, a tenant of another region isn't served here,
    # and LLM calls must go to a provider hosted in it. `region_deployments`
    # maps every region to its operator console's URL, as JSON
    # ('{"EU": "https://ops.eu.axyntro.app"}'), so the console can say
    # where to create a tenant of another region.
    deployment_region: str = ""
    region_deployments: dict[str, str] = Field(default_factory=dict)

    @property
    def operator_host_set(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.operator_hosts.split(",") if h.strip())

    @property
    def oidc_client_secret(self) -> str:
        return os.environ.get(self.oidc_client_secret_env, "")
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
        if self.auth_mode == "saml":
            # Epic 37 build order: SAML (US37.9) isn't built yet. Refusing
            # here beats starting up with a sign-in page that can't work.
            raise ValueError("AUTH_MODE=saml is not available in this build yet -- use AUTH_MODE=oidc")
        if self.auth_mode == "oidc":
            missing = [name for name, value in (("OIDC_ISSUER", self.oidc_issuer), ("OIDC_CLIENT_ID", self.oidc_client_id))
                       if not value]
            if not self.oidc_client_secret:
                missing.append(f"{self.oidc_client_secret_env} (named by OIDC_CLIENT_SECRET_ENV)")
            if missing:
                raise ValueError(f"AUTH_MODE=oidc needs {', '.join(missing)}")
            if self.auth_require_mfa and not (self.auth_mfa_amr_values or self.auth_mfa_acr_values):
                raise ValueError("AUTH_REQUIRE_MFA is on but AUTH_MFA_AMR_VALUES and AUTH_MFA_ACR_VALUES are both empty")
        if self.environment == "production" and not self.auth_require_mfa:
            raise ValueError("AUTH_REQUIRE_MFA=false is not allowed with ENVIRONMENT=production")
        if self.environment == "production" and self.operator_auth_mode == "local":
            raise ValueError("OPERATOR_AUTH_MODE=local is not allowed with ENVIRONMENT=production")
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
