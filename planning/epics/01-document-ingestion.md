# Epic 1 -- Document Ingestion Pipeline

**Status: all 8 stories implemented** (`backend/app/ingestion/`, `backend/app/api/documents.py`). Runs against real Postgres/object-store only once Epic 9 lands -- storage today is local disk + the in-memory store scaffold.

Goal: Accept source process documentation in any supported format and
turn it into raw extracted content (text, tables, shapes/connectors, OCR'd
diagram content) ready for process-information extraction (Epic 3).

Depends on: Epic 9 (platform foundations) for storage/API scaffolding.
Feeds: Epic 2 (knowledge store), Epic 3 (BPMN generation).

## User Stories

US1.1 -- Upload documents via UI. **Implemented.**
As a Process Analyst, I want to upload one or more files (PDF, DOCX, Visio
vsdx, images) via drag-and-drop or file picker, so that I can start
generating a process diagram from my existing documentation.
Frontend: `frontend/src/pages/ProcessDetailPage.tsx` (file picker, multi-select).

US1.2 -- Ingestion API accepts and validates files. **Implemented.**
As a Platform Engineer, I want a backend endpoint that accepts uploads,
validates file type/size, and stores the original file, so that ingestion is
reliable and rejects unsupported input with a clear error.
`POST /api/processes/{id}/documents` -- content-type allowlist + 25MB size
limit, original bytes persisted to local disk (`app/ingestion/storage.py`).

US1.3 -- Text and table extraction (PDF / DOCX). **Implemented.**
As a Process Analyst, I want text and tables extracted from PDF and DOCX
documents (including embedded process steps written as prose or tables), so
that narrative process descriptions can be turned into structured data.
Initial implementation sends the whole extracted document as one LLM call
(no chunking); see
[document-ingestion-strategy.md](../document-ingestion-strategy.md) for the
sizing decision and the chunking fast-follow design.
`app/ingestion/extractors.py` (`extract_pdf`/`extract_docx`) +
`app/ingestion/structuring.py` (`structure_process`).

US1.4 -- Visio diagram parsing. **Implemented.**
As a Process Analyst, I want shapes, connectors, and labels extracted from a
Visio vsdx diagram, so that an existing flowchart can be converted
without redrawing it from scratch.
`app/ingestion/extractors.py` (`extract_vsdx`) -- deterministically parses
the .vsdx zip's page XML for shape text and `<Connect>` topology (so flows
come from the file's actual connectors, not LLM guesswork), then reuses
`structure_process`. Verified against hand-built fixture XML matching the
real VSDX schema (unit tests) -- **not yet verified against a real
Visio-exported file**, which may have quirks the fixture doesn't cover.

US1.5 -- Image ingestion (OCR plus diagram understanding). **Implemented.**
As a Process Analyst, I want to upload a photo or screenshot of a process
flowchart or whiteboard and have its text and shape layout interpreted, so
that even informal or scanned documentation can be used as a source.
`app/ingestion/structuring.py` (`structure_process_from_image`) -- no
deterministic pre-extraction (none exists for images); goes straight to the
LLM's vision input. Verified live against a real generated flowchart image:
correctly identified 3 steps and both connecting arrows.

US1.6 -- Ingestion job status. **Implemented.**
As a Process Analyst, I want to see the status of an ingestion job
(queued/processing/done/failed) per uploaded document, so that I know when
extraction is ready to review, especially for larger files.
Status field on `DocumentDetail`, driven by `app/ingestion/pipeline.py`;
shown in the UI with a live-polling refresh while anything is pending.

US1.7 -- Ingestion error handling. **Implemented.**
As a Process Analyst, I want clear feedback when a file cannot be parsed
(corrupt, unsupported, unreadable scan), so that I can fix or replace it
instead of getting a silent failure.
Content-type/size validation at upload (400s with a specific reason);
parse/structuring failures caught in `pipeline.process_document` and
recorded as `status: "failed"` + `error_message` rather than silently
dropped or left stuck.

US1.8 -- Multi-document merge for one process. **Implemented.**
As a Process Analyst, I want to associate multiple source documents with a
single process (e.g., a written SOP plus a Visio diagram plus a
screenshot), so that the generated diagram reflects all available
documentation rather than just one file.
`app/ingestion/merge.py` -- matches new elements against existing ones by
label similarity (`difflib`, no embeddings yet -- see the decision doc) +
actor agreement; a confirmed match merges (unioning source_refs, keeping
the higher confidence); a similar-label match with *disagreeing* actors is
kept as two separate elements, both downgraded to `confidence: "low"`, per
the skill's conflict-handling rule, rather than one being silently
discarded. This is heuristic string-similarity matching, not semantic
matching -- a known, documented limitation until Epic 2's embeddings exist.

## Notes / Open Questions

Extraction approach: deterministic parsing (`python-docx`, `pdfplumber`,
Visio's zipped-XML format) for PDF/DOCX/Visio; vision-only (no
deterministic pass) for images. See the `process-doc-ingestion` skill.

Large file / many-page handling and cost implications: **decided**, see
[document-ingestion-strategy.md](../document-ingestion-strategy.md) --
simple single-call extraction for now, chunking added as a fast-follow once
real document sizes are seen. Epic 10's cost tracking (US10.3) applies
unchanged to both, and already covers all format types (every extraction
call, regardless of source format, goes through the same `LLMClient.complete()`
tagged `operation="document_extraction"`).

**Known gaps to revisit, not blocking:**
- US1.4's Visio parser is untested against a real Visio-exported `.vsdx` file (only a schema-accurate hand-built fixture).
- US1.8's matching is string-similarity heuristic, not semantic -- revisit once Epic 2's embeddings land.
- No chunking yet for oversized documents (by design -- see the decision doc).
