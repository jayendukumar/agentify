# Document Ingestion: Extraction Sizing & Embedding Strategy

**Status:** Decided (2026-09-17); chunking still not built (by design --
see below). Embedding **is** built now, exactly as designed here: local
`all-MiniLM-L6-v2` via `app/ingestion/embeddings.py`, one row per
extraction block in the `document_embeddings` table (pgvector). Nothing
queries them yet -- see [epics/02-knowledge-store.md](epics/02-knowledge-store.md)'s
"Known gaps".
**Related epics/stories:** Epic 1 -- [US1.3](epics/01-document-ingestion.md), [US1.7](epics/01-document-ingestion.md), [US1.8](epics/01-document-ingestion.md); Epic 2 -- [US2.3](epics/02-knowledge-store.md); Epic 10 -- [US10.3](epics/10-nonfunctional.md)
**Related skill:** [.claude/skills/process-doc-ingestion/SKILL.md](../.claude/skills/process-doc-ingestion/SKILL.md)
**Also see:** [claude-api-access-notes.md](claude-api-access-notes.md) (LLM provider decision this builds on)

## Context

Real process documentation can be long: multi-page SOPs, manuals that bundle
several distinct processes, scanned material with a lot of extracted text.
US1.3 (PDF/DOCX text & table extraction) needs a clear answer to two
questions that are easy to conflate but are actually separate problems:

1. **How much of a document goes into one LLM call for extraction?** (a
   one-time, structural problem -- making the extraction step tractable and
   accurate)
2. **Should document content be embedded for later retrieval?** (an
   ongoing, semantic problem -- letting later features find a relevant
   passage without re-reading/re-sending the whole document every time)

Conflating these leads to the wrong design -- e.g. chunking for embeddings
(small, semantically tight pieces) is the wrong grain for chunking for
extraction (larger, structurally coherent pieces), and vice versa.

## Decision

**Extraction (US1.3): keep it simple for the initial implementation.**
Send the whole deterministically-extracted document text (from
`pdfplumber`/`python-docx`) as one LLM call, as already planned. This
covers the realistic common case -- Qwen3.7 Flash's 1M-token context window
comfortably fits a typical multi-page SOP (a few thousand to tens of
thousands of tokens) -- without building chunking logic against a
hypothetical document size distribution we don't have data on yet.

**Chunking: explicit fast-follow, not built now.** Ship US1.3 without it;
add it once real uploaded documents show it's needed, or proactively once
capacity allows, using the design below so the fast-follow isn't a
redesign -- just filling in the mechanism already specified.

**Embedding: a separate concern, deferred to Epic 2 (US2.3), not part of
US1.3 at all.** Embeddings are for retrieval (chat, re-generation,
multi-doc matching), not for producing the first draft BPMN. They don't
block US1.3 and shouldn't be built alongside it.

## Rationale

### Why extraction chunking is deferred, not skipped

The failure mode isn't "documents that don't fit in context" (1M tokens is
generous) -- it's cost and extraction quality on unusually large inputs:
a very long or table-heavy document produces a proportionally larger, more
expensive call, and LLM extraction accuracy tends to degrade the more is
crammed into one pass. This is a real but *sizing* problem, not a
blocking one, so it's reasonable to ship the simple path first and add a
size-triggered fallback once we know what documents people actually
upload, rather than guessing at thresholds today.

### Why chunking (when built) must be structure-aware, not fixed-size

A process's meaning lives in step order and section boundaries. A naive
token-count sliding window risks slicing "Step 4: submit form for..." apart
from its continuation mid-sentence, corrupting the LLM's ability to
reconstruct sequence and gateway logic. Chunking must instead use
boundaries the deterministic extraction pass already provides for free
(PDF page breaks + detected headings, DOCX heading styles/paragraph
breaks), and must never split a table across chunks.

### Why chunk-merging reuses the US1.8 multi-document merge logic

A single large document split into chunks is structurally the same problem
as multiple documents describing one process: match extracted elements by
label similarity + actor + sequence position, flag disagreements as
low-confidence rather than silently picking one (see the
`process-doc-ingestion` skill's existing multi-document merge rules). This
means the fast-follow needs no new merge logic, only the chunking and the
call-per-chunk loop -- the merge step it feeds into already exists in the
skill's design.

### Why embeddings are deferred rather than built alongside US1.3

Embedding-backed retrieval only matters for what happens *after* the first
draft exists -- Epic 5's "what does this step do", US3.7's re-generation,
US1.8's cross-document element matching. None of those are needed to
produce a first draft BPMN. Building the vector store, chunk-level
embedding pipeline, and retrieval logic now would be scope creep on a
story that doesn't need it, and Epic 2 (US2.3) is the story that actually
owns this.

### Embedding provider choice (for when Epic 2 is built): local model, not OpenRouter

OpenRouter does have a working `/embeddings` endpoint (confirmed working
as of this writing, offering Qwen3 Embedding 8B among others), but the
recommendation for this project is a **local model via
`sentence-transformers`** (e.g. `all-MiniLM-L6-v2`, ~22MB, ~14K
sentences/sec on CPU) instead, for three reasons:

1. **Volume mismatch.** Embedding is a per-paragraph/per-table-row
   operation -- potentially hundreds of calls per document. That's the
   wrong shape for a hosted API tuned for infrequent, larger completions; a
   small local model does it in-process with no network round trip.
2. **Cost is real at that volume, for no quality benefit.** Embedding
   quality needs here are modest (matching paragraphs about the same
   process step within one process's own documents), not
   frontier-model-sensitive -- we are not doing large-corpus semantic
   search.
3. **Decoupling.** Keeps the chat/extraction LLM provider (OpenRouter/
   Qwen3.7 Flash -- itself a cost-driven "for now" decision, see
   [claude-api-access-notes.md](claude-api-access-notes.md)) separate from
   the embedding provider. If that decision changes later, embeddings
   aren't entangled with it. It also fits Epic 9's "local dev, local test"
   framing: no new API key, runs offline.

The honest tradeoff: a 22MB local model is good-not-best on domain jargon
compared to Qwen3 Embedding 8B. That's an acceptable trade for small-scale,
high-precision matching within one process's own documents.

## Design specifics (for the fast-follows, not built yet)

These are captured now so the fast-follow implementation doesn't have to
re-derive them:

**Chunking (Epic 1 fast-follow):**
- Trigger: extracted text exceeding roughly **150K tokens** switches from
  single-call to chunked extraction. Below that, send it whole (current
  US1.3 behavior).
- Target chunk size once triggered: **~30-60K tokens** per extraction call
  -- generous headroom under the 1M context window, small enough to keep
  calls fast/cheap and avoid output-quality degradation from an overstuffed
  prompt.
- Chunk boundaries: page breaks + detected headings (PDF), heading
  styles/paragraph breaks (DOCX); tables stay atomic (never split across
  chunks).
- Overlap: a small window (the last paragraph/section of chunk N repeated
  at the start of chunk N+1) so a step description spanning a boundary
  isn't orphaned. This is for boundary continuity only -- the merge step
  (below) handles actual cross-chunk stitching.
- Merge: reuse the `process-doc-ingestion` skill's US1.8 multi-document
  merge rules unchanged (match on label similarity + actor + sequence
  position; conflicting/ambiguous matches get `confidence: "low"` and
  surface for review rather than being silently resolved).

**Embedding (Epic 2, US2.3):**
- What gets embedded: not the extraction chunks above -- a finer grain,
  one embedding per deterministic-extraction unit (a paragraph, a table
  row/section), each tagged with its `source_ref` (document_id +
  location), matching the canonical schema in the `process-doc-ingestion`
  skill.
- Provider: local `sentence-transformers` model (`all-MiniLM-L6-v2` as the
  default candidate), not OpenRouter's embeddings endpoint. See rationale
  above.
- Store: pgvector on the same local Postgres instance, per the
  `local-stack-bootstrap` skill's existing default (avoids standing up a
  separate vector DB service for local dev).

## What this resolves

This closes the open question left in
[epics/01-document-ingestion.md](epics/01-document-ingestion.md) ("Large
file / many-page handling and cost implications should be considered
together with Epic 10") -- it's no longer open, it's decided above, with
Epic 10's cost tracking (US10.3) applying to both the current single-call
path and the future chunked path without any change to how usage is
logged (each call, whether one whole-document call or one
chunk-of-many, is just another `LLMClient.complete()` call tagged with the
same `operation` label).
