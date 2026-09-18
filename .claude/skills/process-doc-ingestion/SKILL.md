---
name: process-doc-ingestion
description: Use when extracting structured business-process information (activities, actors, decision points, inputs/outputs, systems) from ingested source documents -- PDF, DOCX, Visio (.vsdx), or images -- in the Agentic Solution Generator project. Normalizes heterogeneous document types into the project's canonical process schema before BPMN generation.
---

# Process Documentation Ingestion

This skill governs how raw uploaded documents become structured process data
(planning epic: `planning/epics/01-document-ingestion.md` and
`planning/epics/02-knowledge-store.md`).

**Status: implemented for PDF/DOCX/Visio/images** -- `backend/app/ingestion/`
(`extractors.py`, `structuring.py`, `merge.py`, `pipeline.py`). This document
still describes the design because it's the reference for maintaining and
extending that code, not just a pre-build spec.

## Canonical process schema (target output)

Every ingestion path -- regardless of source format -- must normalize into
this shape before it reaches the BPMN generation step:

```json
{
  "process_name": "string",
  "actors": [
    {"id": "actor-1", "name": "string", "type": "role|system|external_party"}
  ],
  "elements": [
    {
      "id": "el-1",
      "type": "task|decision|start_event|end_event|intermediate_event",
      "label": "string",
      "actor_id": "actor-1",
      "inputs": ["string"],
      "outputs": ["string"],
      "systems_touched": ["string"],
      "source_refs": [
        {"document_id": "doc-1", "location": "page 3 / cell B4 / shape 12", "excerpt": "string"}
      ],
      "confidence": "high|medium|low"
    }
  ],
  "flows": [
    {"id": "f-1", "from": "el-1", "to": "el-2", "condition": "string|null"}
  ]
}
```

`source_refs` and `confidence` are mandatory on every element -- they feed
Epic 2's traceability requirement (US2.5) and Epic 3's confidence-flagging
requirement (US3.6). Never emit an element without at least one source
reference.

## Per-format extraction approach

Implemented: `app/ingestion/extractors.py` (`extract_pdf`, `extract_docx`,
`extract_vsdx`) + `app/ingestion/structuring.py` (`structure_process`,
`structure_process_from_image`).

- **PDF / DOCX (prose and tables):** deterministic text/table extraction
  first (e.g. `pdfplumber`/`pypdf`, `python-docx`), then pass the extracted
  text to the configured LLM to identify process steps, actors, decisions, and sequence.
  Do not ask the LLM to do layout-aware PDF parsing from raw bytes -- give it
  clean extracted text/tables.
- **Visio (`.vsdx`):** a `.vsdx` file is a zip archive of XML parts. Parse
  `visio/pages/page1.xml` (or equivalent) for shapes (`<Shape>`), their text
  (`<Text>`), and connectors (`<Connect>` entries linking shape IDs) using a
  deterministic XML parser first -- this gives exact shape labels and graph
  topology without LLM guessing. Only fall back to an LLM pass over the
  extracted shape/connector list to resolve ambiguous shape roles (e.g. is
  this a decision diamond or just a styled rectangle).
- **Images (photos, screenshots, scanned diagrams):** use the configured
  LLM's vision input directly on the image (the current default, Qwen3.7
  Flash via OpenRouter, accepts image input) -- do not pre-OCR with a
  separate tool unless
  the image quality is very poor. Ask explicitly for: text labels, shape
  types (box/diamond/circle), and the connections between them (by label,
  since spatial coordinates from vision are unreliable for exact graph
  reconstruction). Always keep the original image in the document store so a
  human can verify the vision interpretation.

## Document size handling (US1.3)

**Current implementation: no chunking.** Send the whole deterministically-
extracted text (from the per-format extraction above) as one LLM call. This
is a deliberate decision, not an oversight -- see
`planning/document-ingestion-strategy.md` for the full rationale. Do not
add chunking speculatively; it's an explicit fast-follow, triggered by real
document sizes, not built alongside the initial US1.3 implementation.

**When the fast-follow is picked up**, the design is already decided (see
that doc for the full write-up) -- do not re-derive it:
- Trigger chunked extraction above ~150K extracted tokens; below that,
  keep sending the document whole.
- Chunk on structural boundaries already available from extraction (PDF
  page breaks + headings, DOCX heading styles/paragraph breaks) at a
  ~30-60K token target size -- never split a table across chunks.
- Small overlap between adjacent chunks for boundary continuity.
- Merge chunk results using the exact same rules as multi-document merge
  below -- a chunked document is the same merge problem as multiple
  documents about one process.

This section governs extraction sizing only. Embedding documents for
retrieval (chat, re-generation, cross-document matching) is a separate,
also-deferred concern owned by Epic 2 (US2.3) -- see the same decision doc
for that design (local `sentence-transformers` model, not the LLM
provider's embeddings endpoint). Nothing in this skill should embed
anything until that story is built.

## Multi-document merge (US1.8)

Implemented: `app/ingestion/merge.py` (`merge_process_schemas`).

When multiple source documents describe one process:
1. Extract each document independently into the canonical schema above.
2. Merge by matching on element label similarity + actor + position in
   sequence -- not exact string match (documents will phrase the same step
   differently). Current implementation matches on label similarity
   (`difflib`, threshold 0.85) + actor agreement only -- sequence-position
   matching is not yet factored in, and this is string similarity, not
   semantic similarity (no embeddings until Epic 2). Revisit both when
   Epic 2's embeddings land.
3. On conflict (two documents disagree on an element's actor, order, or
   existence), keep both candidate values, mark `confidence: "low"`, and
   surface the conflict for human review rather than silently picking one.
4. Preserve `source_refs` from every contributing document on a merged
   element -- never collapse to a single source when multiple exist.

## Confidence scoring

- `high`: element and its sequencing are explicitly and unambiguously
  stated in a single clear source passage.
- `medium`: element is stated but some detail (actor, exact trigger
  condition, position in sequence) was inferred rather than explicit.
- `low`: element was inferred from indirect evidence, or documents
  conflict about it.

Never silently upgrade a `low`/`medium` confidence element to `high` to
make a diagram look cleaner -- confidence flows through to Epic 3's
human-review flagging (US3.6) and must stay honest.

## Failure handling (US1.7)

If a document cannot be parsed at all (corrupt file, password-protected PDF,
unsupported Visio variant, unreadable scan), fail that document explicitly
with a specific reason -- never silently skip it or emit a partial/empty
schema for it without surfacing the failure to the ingestion job status.
