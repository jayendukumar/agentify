# Epic 9 -- Platform Foundations

Goal: The engineering scaffolding every other epic depends on -- backend
API, frontend app, databases, and a local development/test environment.

Depends on: nothing (this is the starting point).
Feeds: every other epic.

## User Stories

US9.1 -- Backend API service scaffold.
As a Platform Engineer, I want a Python backend service (e.g. FastAPI) with
an OpenAPI spec, so that the frontend and any future integrations have a
documented, typed API contract to build against.

US9.2 -- Frontend application scaffold.
As a Platform Engineer, I want a Node.js/React frontend scaffold that
consumes the backend API, so that UI feature epics (4, 5, 8) have a
consistent app shell, routing, and API client to build on.

US9.3 -- LLM API integration layer.
As a Platform Engineer, I want a shared backend module for calling the
configured LLM API (auth/config, model selection per call type, prompt
templates, retries/error handling) behind a provider-agnostic interface, so
that every feature that needs an LLM call (ingestion, BPMN generation,
chat, blueprint evaluation) doesn't reimplement this, and the underlying
provider can change without touching feature code. Current default:
OpenRouter routing to Qwen3.7 Flash (`qwen/qwen3.7-flash`), chosen for cost
-- see `planning/claude-api-access-notes.md` for the provider comparison and
the `local-stack-bootstrap` skill for the request shape.

US9.4 -- Relational database setup.
As a Platform Engineer, I want a local Postgres instance with migrations
wired up, so that Epic 2's data model has somewhere to live in local
development.

US9.5 -- Vector database setup.
As a Platform Engineer, I want a local vector store (e.g. pgvector, or a
standalone store) wired up, so that Epic 2's embeddings have somewhere to
live in local development.

US9.6 -- Document/object store setup.
As a Platform Engineer, I want a local object store (e.g. MinIO, or a local
filesystem abstraction behind the same interface), so that Epic 2's document
storage works locally and could later point at a real cloud object store
with minimal code change.

US9.7 -- Docker Compose local environment.
As a Platform Engineer, I want a docker-compose setup that brings up the
backend, frontend, database, vector store, and object store together, so
that the whole stack can be run and tested locally with one command.

US9.8 -- Local test setup.
As a Platform Engineer, I want unit and integration tests runnable locally
for both backend (pytest) and frontend (vitest/jest), so that features can
be verified without a deployed environment.

US9.9 -- Local auth/session handling.
As a Platform Engineer, I want basic session/auth handling suitable for
single-user or small-team local usage, so that per-user actions (finalize,
override) can be attributed without building full enterprise auth yet.

US9.10 -- Environment and secrets management.
As a Platform Engineer, I want environment variables and secrets (LLM
provider API key, DB credentials) managed via a git-ignored local config
file and never committed to source control, so that credentials stay safe
by default.

## Notes / Open Questions

This epic should be seeded first, in parallel with Epic 1/2's design work,
since nothing else can be built or tested without it. See the
local-stack-bootstrap skill for concrete service/port/env conventions to
apply as this epic is implemented.
