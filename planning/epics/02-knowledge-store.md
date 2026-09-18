# Epic 2 -- Business Process Knowledge Store

**Status: all 6 stories implemented** (`backend/app/db/`, `backend/migrations/`). Verified with a real restart: created a process, uploaded a document, killed the backend process entirely, started a fresh one, and the process/actors/elements/flows/source_refs were all still there, loaded from Postgres.

Goal: Persist structured business-process information, its source
provenance, and vector embeddings so it can be retrieved, edited, audited,
and versioned throughout the product's lifecycle.

Depends on: Epic 9 (DB/infra scaffolding) -- partially: this epic stood up
its own `docker-compose.yml` `db` service rather than waiting on Epic 9's
full stack, since Epic 9 (US9.7) hadn't been built yet and this epic needed
Postgres to exist at all. Epic 9 should adopt/extend this compose file
rather than create a competing one.
Feeds: Epic 3 (BPMN generation reads/writes this store), Epic 5 (chat
retrieval/context), Epic 6 (versioning).

## User Stories

US2.1 -- Canonical process data model. **Implemented.**
As a Platform Engineer, I want a defined schema for a business process
(activities, actors/roles, decision points, inputs/outputs, systems touched,
sequencing), so that every ingestion source normalizes into one consistent
representation before BPMN generation.
Pydantic schema: `app/schemas/common.py` (unchanged from Epic 1). ORM
mirror for persistence: `app/db/models.py`.

US2.2 -- Relational DB schema and migrations. **Implemented.**
As a Platform Engineer, I want relational tables (with migrations) for
processes, elements, documents, versions, and users, so that configuration
and process metadata are reliably persisted and evolvable.
Postgres via `docker-compose.yml`; Alembic migrations in
`backend/migrations/`. Tables: `processes`, `documents`, `actors`,
`process_elements`, `source_refs`, `process_flows`, `document_embeddings`,
`process_schema_changes`. Deliberately does *not* yet cover draft BPMN,
chat messages, finalized diagram versions, the blueprint overlay, or users
-- those stay in the in-memory scaffold (`app/store.py`) until Epics
3/5/6/7/8 (and auth, Epic 9) are actually implemented; persisting
placeholder data for unbuilt features would have been scope creep on this
epic. See `app/db/models.py`'s module docstring.

US2.3 -- Vector store for document embeddings. **Implemented.**
As a Platform Engineer, I want embeddings of ingested document chunks stored
in a vector database, so that extraction and chat editing can retrieve
relevant source context instead of re-reading entire documents every time.
pgvector on the same Postgres instance; local `sentence-transformers`
(`all-MiniLM-L6-v2`, 384-dim) via `app/ingestion/embeddings.py` -- not the
LLM provider's API, see [document-ingestion-strategy.md](../document-ingestion-strategy.md)
for the rationale. One embedding row per deterministic-extraction block
(paragraph/table/Visio shape or connector), written alongside the LLM
structuring call in `app/ingestion/pipeline.py`. Not yet wired: nothing
*queries* these embeddings yet -- Epic 5 (chat retrieval) and US1.8's
matching are the features that will, and don't exist yet either.

US2.4 -- Document/object store. **Implemented (local disk, not an object store).**
As a Platform Engineer, I want original uploaded files and derived artifacts
(extracted text, generated BPMN XML, blueprint reports) stored in an object
store, so that they are retrievable independent of the database and diagrams
can always be regenerated or audited against source.
Original files: local disk (`app/ingestion/storage.py`, unchanged from
Epic 1) -- an accepted simplification per the `local-stack-bootstrap`
skill, not MinIO/S3. Document *metadata* (filename, status, etc.) is now
in Postgres (`documents` table) rather than only in memory. Derived
artifacts (generated BPMN XML, blueprint reports) aren't persisted yet --
those features don't exist yet either (Epic 3, Epic 8).

US2.5 -- Traceability from BPMN elements to source. **Implemented (schema level; BPMN doesn't exist yet).**
As a Process Analyst, I want each generated BPMN node to link back to the
source document passage(s) it was derived from, so that I can verify
correctness and understand why the system produced a given step.
`source_refs` table, FK'd to both the owning element and the source
document, unchanged in spirit from Epic 1's in-memory version -- just
durable now. Once Epic 3 generates real BPMN XML, its elements need to
carry these refs through (e.g. as extension attributes) for this to be
visible in the diagram itself, not just the underlying schema.

US2.6 -- Process knowledge versioning. **Implemented (append-only log, not full versioning).**
As a Process Analyst, I want changes to the process's underlying data (not
just the diagram) tracked as the diagram evolves, so that I can see how my
understanding of the process changed over time, not just the visual layout.
`process_schema_changes` table: one row per merge, recording which document
triggered it and a plain-text summary (e.g. "Merged 3 new step(s); total
steps 5 -> 7"). This is a change *log*, not a queryable history of prior
schema states (no "view the schema as it was after document 2" yet) --
revisit if that finer-grained capability turns out to be needed.

## Notes / Open Questions

Vector DB choice for local dev: **decided and built** -- pgvector on the
same Postgres instance, per the `local-stack-bootstrap` skill and this
epic's `docker-compose.yml`.

US2.6 overlaps with Epic 6's diagram versioning -- Epic 6 owns
diagram-level versions; this story owns the underlying data model changes
that versioning is built on.

**A real bug this epic's build surfaced and fixed, worth knowing about
elsewhere in the codebase:** the LLM assigns element/actor ids like
"el-1"/"actor-1" that are only unique *within one extraction call* -- two
different documents (or two images) can each legitimately produce "el-1"
for completely different things. Fixed in `app/ingestion/structuring.py`
(`_assign_globally_unique_ids`) by replacing every id with a globally-unique
one immediately after parsing the LLM's response, before it reaches
`app/ingestion/merge.py` or the database. This was silently wrong in Epic
1's in-memory version; a real Postgres primary key is what actually forced
it to the surface.

**Known gaps to revisit, not blocking:**
- Embeddings are written but nothing reads them yet (waits on Epic 5 / US1.8's real semantic matching).
- `process_schema_changes` is append-only, not a queryable point-in-time history.
- No `users` table (Epic 9 owns auth, US9.9 -- not implemented).
- Draft BPMN / chat / versions / blueprint remain in-memory (`app/store.py`) pending Epics 3/5/6/7/8.
