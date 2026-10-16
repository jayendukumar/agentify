import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    agents,
    auth,
    batches,
    blueprint,
    bpmn,
    catalog,
    connectors,
    datasets,
    operator,
    oracles,
    chat,
    documents,
    environments,
    evaluation,
    directory,
    gap_analysis,
    governance,
    oidc,
    orchestration,
    organization,
    process_twin,
    processes,
    publish,
    registries,
    results,
    scim,
    twin,
    twin_builder,
    usage,
    versions,
)
from app.config import get_settings, settings_or_exit
from app.context.repository import ContextValidationError
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMBudgetError,
    LLMError,
    LLMPolicyError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.request_context import configure_logging, request_id_middleware
from app.scim.protocol import ScimError
from app.store import NotFoundError
from app.tenancy.middleware import TenantMiddleware

logger = logging.getLogger("app.main")


# Epic 37, US37.14: e.g. AUTH_MODE=local with ENVIRONMENT=production stops
# uvicorn here, with the reason, before anything binds a port.
settings_or_exit("API")
configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Epic 31, US31.1: run the durable job worker alongside the API unless
    it's disabled (tests; or a deployment running `python -m
    app.jobs.worker` separately).

    Epic 38, US38.2: checks first that the database enforces tenant
    isolation for the role the API connects as. Production refuses to
    start without it."""
    _check_tenant_isolation(app)
    worker = task = None
    if get_settings().job_worker_enabled:
        from app.jobs.worker import Worker

        worker = Worker()
        task = asyncio.create_task(worker.run())
    try:
        yield
    finally:
        if worker is not None and task is not None:
            worker.stop()
            await task


def _check_tenant_isolation(app: FastAPI) -> None:
    from app.db.session import isolation_enforced

    try:
        enforced = isolation_enforced()
    except Exception as exc:  # the database is down: the first request will say so
        if get_settings().environment == "production":
            raise
        logger.warning("Couldn't check tenant isolation: %s", exc)
        app.state.tenant_isolation = "unknown"
        return
    app.state.tenant_isolation = "enforced" if enforced else "not enforced"
    if enforced:
        return
    message = (
        "DATABASE_URL connects as a superuser or a role with BYPASSRLS, so row-level security doesn't apply and "
        "tenants aren't kept apart. Connect as the app role (axyntro_app) and keep the owner for DATABASE_OWNER_URL."
    )
    if get_settings().environment == "production":
        raise RuntimeError(message)
    logger.warning(message)


app = FastAPI(
    lifespan=lifespan,
    title="Agentic Solution Generator API",
    description="Backend API for ingesting process documentation, generating as-is BPMN, "
    "chat-driven editing, and agentic blueprint evaluation.",
    version="0.1.0",
)

# Local dev only (Epic 9): the React dev server runs on 3000, the API on 8000.
# Both localhost and 127.0.0.1 are listed -- browsers treat them as distinct
# origins even though they're the same machine, and the frontend is pinned
# to 127.0.0.1 (see frontend/vite.config.ts) to avoid an IPv6-only binding
# issue, so both must be allowed here or CORS silently blocks every request.
# Epic 38, US38.1: added first, so it runs inside CORS -- preflights are
# answered without resolving a tenant, and a 404 for an unknown host still
# carries CORS headers the browser can read.
app.add_middleware(TenantMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    # Epic 38: each tenant's frontend calls the API from its own host.
    allow_origin_regex=get_settings().cors_origin_regex,
    allow_credentials=True,  # Epic 9/10, US9.9: the session cookie needs this to cross the :3000/:8000 origin gap
    allow_methods=["*"],
    allow_headers=["*"],
    # Epic 37, US37.12: tells the frontend a 403 was a stale CSRF token.
    expose_headers=["X-Auth-Error"],
)

app.middleware("http")(request_id_middleware)

app.include_router(auth.router)
app.include_router(operator.router)
app.include_router(operator.host_router)
app.include_router(oidc.router)
app.include_router(directory.router)
app.include_router(scim.router)
app.include_router(governance.router)
app.include_router(usage.router)
app.include_router(processes.router)
app.include_router(documents.router)
app.include_router(bpmn.router)
app.include_router(chat.router)
app.include_router(versions.router)
app.include_router(blueprint.router)
app.include_router(gap_analysis.router)
app.include_router(agents.router)
app.include_router(registries.router)
app.include_router(twin.router)
app.include_router(twin_builder.router)
app.include_router(orchestration.router)
app.include_router(environments.router)
app.include_router(evaluation.router)
app.include_router(publish.router)
app.include_router(organization.router)
app.include_router(organization.process_router)
app.include_router(organization.pack_router)
app.include_router(catalog.router)
app.include_router(catalog.process_router)
app.include_router(connectors.router)
app.include_router(connectors.jobs_router)
app.include_router(datasets.router)
app.include_router(oracles.router)
app.include_router(batches.router)
app.include_router(results.router)
app.include_router(process_twin.router)


app.add_exception_handler(ScimError, scim.scim_error_handler)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Epic 37, US37.10: an IdP's malformed SCIM request gets a SCIM error,
    not FastAPI's own format. Every other route keeps the default."""
    if request.url.path.startswith("/scim/"):
        return await scim.scim_error_handler(request, ScimError(400, str(exc.errors()[:3]), "invalidSyntax"))
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ContextValidationError)
def handle_context_validation(request: Request, exc: ContextValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(LLMPolicyError)
def handle_llm_policy(request: Request, exc: LLMPolicyError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": f"Blocked by the model policy: {exc}"})


@app.exception_handler(LLMBudgetError)
def handle_llm_budget(request: Request, exc: LLMBudgetError) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(LLMAuthenticationError)
def handle_llm_auth_error(request: Request, exc: LLMAuthenticationError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": f"LLM provider authentication failed: {exc}"})


@app.exception_handler(LLMBadRequestError)
def handle_llm_bad_request(request: Request, exc: LLMBadRequestError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"LLM provider rejected the request: {exc}"})


@app.exception_handler(LLMRateLimitError)
def handle_llm_rate_limit(request: Request, exc: LLMRateLimitError) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": f"LLM provider rate limit exceeded: {exc}"})


@app.exception_handler(LLMTimeoutError)
def handle_llm_timeout(request: Request, exc: LLMTimeoutError) -> JSONResponse:
    return JSONResponse(status_code=504, content={"detail": f"LLM provider request timed out: {exc}"})


@app.exception_handler(LLMError)
def handle_llm_error(request: Request, exc: LLMError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"LLM provider error: {exc}"})


@app.get("/healthz", tags=["health"])
def healthz(request: Request) -> dict[str, str]:
    # Epic 37, US37.14: lets an operator see at a glance how sign-in is set
    # up. Epic 38: and whether the database keeps tenants apart. Answers on
    # any host (app/tenancy/middleware.py), and names no tenant.
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
        "auth_mode": settings.auth_mode,
        "tenant_isolation": getattr(request.app.state, "tenant_isolation", "unknown"),
    }
