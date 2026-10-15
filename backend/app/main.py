import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
    oracles,
    chat,
    documents,
    environments,
    evaluation,
    gap_analysis,
    governance,
    orchestration,
    organization,
    process_twin,
    processes,
    publish,
    registries,
    results,
    twin,
    twin_builder,
    versions,
)
from app.config import get_settings, settings_or_exit
from app.context.repository import ContextValidationError
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMError,
    LLMPolicyError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.request_context import RequestIDLogFilter, request_id_middleware
from app.store import NotFoundError


def _configure_logging() -> None:
    """Epic 10: console output alone (the previous logging.basicConfig)
    doesn't survive past the terminal it ran in -- app_log_enabled adds a
    rotating-free (dev-scale, see the app_log_path setting) file handler
    alongside it, so request/LLM-call/error logs persist across restarts
    for later review, not just the current session's console scrollback.
    US10.1: every handler also gets RequestIDLogFilter so %(request_id)s
    is always populated, in or out of a request (see app/request_context.py).
    """
    settings = get_settings()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.app_log_enabled:
        settings.app_log_file_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings.app_log_file_path, encoding="utf-8"))
    request_id_filter = RequestIDLogFilter()
    for handler in handlers:
        handler.addFilter(request_id_filter)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,  # re-configure cleanly even if something already called basicConfig
    )



# Epic 37, US37.14: e.g. AUTH_MODE=local with ENVIRONMENT=production stops
# uvicorn here, with the reason, before anything binds a port.
settings_or_exit("API")
_configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Epic 31, US31.1: run the durable job worker alongside the API unless
    it's disabled (tests; or a deployment running `python -m
    app.jobs.worker` separately)."""
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,  # Epic 9/10, US9.9: the session cookie needs this to cross the :3000/:8000 origin gap
    allow_methods=["*"],
    allow_headers=["*"],
    # Epic 37, US37.12: tells the frontend a 403 was a stale CSRF token.
    expose_headers=["X-Auth-Error"],
)

app.middleware("http")(request_id_middleware)

app.include_router(auth.router)
app.include_router(governance.router)
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


@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ContextValidationError)
def handle_context_validation(request: Request, exc: ContextValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(LLMPolicyError)
def handle_llm_policy(request: Request, exc: LLMPolicyError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": f"Blocked by the model policy: {exc}"})


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
def healthz() -> dict[str, str]:
    # Epic 37, US37.14: lets an operator see at a glance how sign-in is set up.
    settings = get_settings()
    return {"status": "ok", "environment": settings.environment, "auth_mode": settings.auth_mode}
