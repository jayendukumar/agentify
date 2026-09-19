---
name: local-stack-bootstrap
description: Use when setting up, running, or troubleshooting the Agentic Solution Generator local development stack (React/Node UI, Python/FastAPI backend, Postgres config DB, vector DB, document object store) via Docker Compose. Covers target service layout, ports, env var conventions, and bring-up order.
---

# Local Stack Bootstrap

This skill governs the local development environment (planning epic:
`planning/epics/09-platform-foundations.md`). The project targets local
development and local testing only -- no cloud deployment assumed yet.

**`db`/`api`/`web` are all implemented** (Epic 2, Epic 9 US9.7):
`docker-compose.yml` at the repo root brings up all three with
`docker compose up -d --build` -- `db` (`pgvector/pgvector:pg16`),
`api` (`backend/Dockerfile`, simple built container, not hot-reload --
runs `alembic upgrade head` then `uvicorn` on start), `web`
(`frontend/Dockerfile`, multi-stage `vite build` -> `nginx` serving the
static bundle, with an SPA `try_files` fallback in `frontend/nginx.conf`).
**Requires `backend/.env` to exist first** (copy from
`backend/.env.example`, fill in `OPENROUTER_API_KEY`) -- `api`'s
`env_file` reads it; `DATABASE_URL` is overridden in compose to point at
`db` by its service name, not `127.0.0.1`. This is for one-command
full-stack bring-up/demo/CI, not the primary local dev loop -- iterate via
`.venv`/`uvicorn --reload` and `npm run dev` as before; Docker doesn't
hot-reload here on purpose (a deliberate simplicity choice, see the
decision log).

## Target service layout

| Service | Tech | Default local port | Notes |
|---|---|---|---|
| `web` | Node.js / React | 3000 | Frontend app; talks to `api` over HTTP |
| `api` | Python / FastAPI | 8000 | Backend API; talks to `db`, `vector-db`, `object-store`, and the configured LLM API (OpenRouter) |
| `db` | PostgreSQL | 5432 | Relational store: processes, elements, versions, users (Epic 2, Epic 9) |
| `vector-db` | pgvector extension on `db`, or a standalone store (Chroma/Qdrant) | 5432 (pgvector) or its own port | Default to pgvector on the same Postgres instance to minimize local moving parts, unless embeddings volume/query patterns later justify a standalone store |
| `object-store` | MinIO (S3-compatible), or local filesystem behind a storage-interface abstraction | 9000 | Original documents + derived artifacts (Epic 2, US2.4) |

Prefer the smallest number of moving parts that satisfies the epics --
pgvector-on-Postgres over a separate vector DB service, and a filesystem
abstraction over MinIO, are both acceptable simplifications for early local
development as long as the code is written behind an interface that could
later swap to a "real" service without touching calling code.

## Environment variable conventions

- `OPENROUTER_API_KEY` -- OpenRouter API key (see
  `planning/claude-api-access-notes.md` for the provider cost comparison
  behind this choice). Current default LLM: `qwen/qwen3.7-flash` (Qwen3.7
  Flash) routed through OpenRouter -- see "LLM provider" below.
- `LLM_MODEL` -- the OpenRouter model ID to use (defaults to
  `qwen/qwen3.7-flash`); keep this as a config value, not hardcoded, so the
  model can be swapped per the notes in `planning/claude-api-access-notes.md`
  without a code change.
- `DATABASE_URL` -- Postgres connection string for `db`; also doubles as
  the vector store connection string (pgvector lives on the same instance,
  so there is no separate `VECTOR_DB_URL`). Default:
  `postgresql+psycopg://agentic:agentic@127.0.0.1:5432/agentic_solution_generator`,
  matching `docker-compose.yml`'s `db` service credentials.
- `EMBEDDING_MODEL_NAME` -- local `sentence-transformers` model for US2.3
  (default `all-MiniLM-L6-v2`, 384-dim); not an API call, see
  `planning/document-ingestion-strategy.md`.
- `OBJECT_STORE_ENDPOINT` / `OBJECT_STORE_BUCKET` -- not implemented; US2.4
  uses local disk (`DOCUMENT_STORAGE_PATH`, see `app/config.py`) instead,
  an accepted simplification per this skill's "smallest number of moving
  parts" note above.
- `API_BASE_URL` -- used by the `web` service to reach `api`.

All secrets live in a git-ignored `.env` file locally (`.env.example`
checked in with placeholder values) -- never commit real credentials (Epic
9, US9.10).

## LLM provider (currently OpenRouter -> Qwen3.7 Flash)

OpenRouter exposes an OpenAI-compatible `/chat/completions` API, so the
backend's LLM integration layer (Epic 9, US9.3) should be built against
that shape rather than a provider-specific SDK:

- Base URL: `https://openrouter.ai/api/v1`
- Auth: `Authorization: Bearer <OPENROUTER_API_KEY>` header
- Model ID: `qwen/qwen3.7-flash` (read from `LLM_MODEL`, not hardcoded)
- An official/community OpenAI-compatible client library works unchanged
  against this base URL -- do not hand-roll raw HTTP calls if an
  OpenAI-compatible Python client is already a dependency.
- Qwen3.7 Flash supports vision (image content blocks), tool/function
  calling, and structured/JSON output -- all of which this project's
  ingestion (Epic 1), BPMN generation (Epic 3), and blueprint evaluation
  (Epic 7) pipelines rely on. Confirm the exact request shape for each
  (image content blocks, tool schema, JSON mode) against OpenRouter's docs
  for this model before building each integration point, since exact
  parameter names can differ slightly from a pure OpenAI client.
- Keep this behind an internal interface (e.g. a `LLMClient` abstraction)
  rather than calling OpenRouter directly from feature code -- this was a
  cost-driven provider choice ("for now", per the project decision log in
  `planning/claude-api-access-notes.md`) and may change as the project
  matures; isolate the blast radius of a future switch.

## Bring-up order

1. `db` (and `vector-db` if a separate service) -- wait for healthy before
   starting anything that depends on it.
2. Run migrations against `db`.
3. `object-store` -- create the default bucket if it doesn't exist.
4. `api` -- depends on `db`/`vector-db`/`object-store` being ready and
   `OPENROUTER_API_KEY` being set; fail fast with a clear error if the key is
   missing rather than starting in a broken state.
5. `web` -- depends on `api` being reachable.

In `docker-compose.yml`, express this with `depends_on` plus healthchecks
(not just service startup order) -- Postgres and MinIO both report "started"
before they're actually ready to accept connections.

## Testing conventions

- Backend: `pytest`, with integration tests that spin up against the
  docker-compose `db`/`vector-db`/`object-store` services (or lightweight
  local equivalents/testcontainers) rather than mocking the database away
  entirely -- the ingestion -> BPMN -> blueprint pipeline has enough
  cross-service behavior that pure unit tests with mocks will miss real
  bugs. **Implemented**: tests run against a dedicated
  `agentic_solution_generator_test` database (same `db` container, separate
  DB -- not the dev one), created once with
  `CREATE DATABASE agentic_solution_generator_test` + `alembic upgrade
  head` against that URL. `tests/conftest.py` sets `DATABASE_URL` to it and
  truncates every table before each test (an autouse fixture) -- not a
  wrapping transaction rolled back after the test, because
  `app/ingestion/pipeline.py` deliberately opens its own DB session for
  background tasks (see that module's docstring), which a wrapping
  transaction on the test's connection wouldn't be able to see.
- Frontend: `vitest` or `jest` + React Testing Library for component tests;
  consider a lightweight end-to-end check (e.g. Playwright) for the
  upload -> diagram -> chat-edit -> finalize -> blueprint flow once those
  epics exist, since this flow is the product's core value path.

## Troubleshooting checklist

- `api` fails to start: check `OPENROUTER_API_KEY` is set and `db`/
  `vector-db`/`object-store` healthchecks are passing first.
- Frontend can't reach backend: check `API_BASE_URL` matches the `api`
  service's compose network name/port, not `localhost`, when both run in
  containers.
- Vector search returns nothing: confirm the pgvector extension is actually
  enabled on `db` (`CREATE EXTENSION IF NOT EXISTS vector;`) as part of
  migrations, not just installed in the image.
