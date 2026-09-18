import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import blueprint, bpmn, chat, documents, processes, versions
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.store import NotFoundError

logging.basicConfig(level=logging.INFO)

app = FastAPI(
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
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(processes.router)
app.include_router(documents.router)
app.include_router(bpmn.router)
app.include_router(chat.router)
app.include_router(versions.router)
app.include_router(blueprint.router)


@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


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
    return {"status": "ok"}
