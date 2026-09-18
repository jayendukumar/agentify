# Decision Log

A running record of *why*, not just *what*. The epics
(`planning/epics/`) and the code itself track what got built; this file
exists for the judgment calls behind it -- design trade-offs, scope cuts,
and defects that testing (not reasoning) uncovered -- so a future session
or a human reviewer can see how a choice was reached without re-deriving
it from scratch.

**What earns an entry here:** a real design/architecture trade-off where
more than one reasonable option existed; a defect found by actually
running the code against real data/APIs/servers (not just passing tests
against mocks); a moment where reality contradicted an assumption made
upstream. Routine implementation and obvious fixes don't need an entry --
see the `decision-log` skill for the exact bar and the maintenance
process this file follows going forward.

---

## 2026-09-17 to 2026-09-18 -- LLM access layer, Epic 1, Epic 2, Epic 3

### Provider choice: OpenRouter -> Qwen3.7 Flash, not Claude

A market-wide cost scan (not just Anthropic pricing) found Qwen3.7 Flash
via OpenRouter at roughly 1/30th the price of Claude Haiku 4.5 (the
cheapest Claude model) while still supporting vision, tool-calling, and a
1M-token context -- the actual feature bar this project needs. Chosen
deliberately as a **cost-driven "for now" decision**, with the real
counter-consideration named up front rather than glossed over: the
cheapest tier is Chinese-hosted, which matters once real (non-test)
business documents are ingested. See `planning/claude-api-access-notes.md`.

The LLM integration itself (`app/llm/client.py`) was built behind a
provider-agnostic `LLMClient` interface on purpose -- feature code never
calls OpenRouter directly -- specifically so that if the provider decision
changes later (likely, given it was explicitly "for now"), the blast
radius is one module, not every call site.

### Token usage instrumentation: dual output, not just logging

When asked to add usage instrumentation, the design used two outputs
instead of one: an in-memory tracker (live totals during a session) *and*
a durable JSONL log (`*.jsonl`, survives restarts, inspectable across
runs). A single in-memory-only tracker would have been simpler but useless
for "how much have I spent this week" -- the actual question a cost
tracker exists to answer. Cost estimates are explicitly opt-in per model
(unpriced models report "unknown", never a guessed number) -- silently
wrong cost data is worse than admittedly-missing data.

### US1.3 chunking/embedding: two different problems, don't conflate them

When asked to think through chunking and embedding ahead of LLM
processing, the key move was separating two problems that look like one:
**chunking for extraction** (a one-time, structural problem -- how much of
a document fits in one LLM call) vs. **embedding for retrieval** (an
ongoing, semantic problem -- finding a relevant passage later without
re-reading the whole document). Conflating them would have produced the
wrong grain for both (extraction wants large, structurally-coherent
pieces; embedding wants small, semantically-tight ones).

Decision: keep US1.3 simple (no chunking, whole document per call) since
Qwen's 1M context comfortably covers the realistic case, and defer
chunking as an explicit fast-follow triggered by real document sizes --
not built speculatively against a size distribution nobody had data on
yet. Embedding got its own separate decision: a local `sentence-transformers`
model, not the LLM provider's embeddings API, because the *volume* shape
is wrong for a hosted API (one call per paragraph/table, potentially
hundreds per document) even though OpenRouter does offer embeddings.
Full writeup: `planning/document-ingestion-strategy.md`.

### Defects found by running US1.3 against the real API, not just mocks

Three separate bugs surfaced only once real documents went through the
real OpenRouter/Qwen3.7 Flash endpoint -- none of them were visible in
mocked unit tests, because the mocks encoded the *intended* behavior, not
what the model actually does:

- **`document_id` not trustworthy.** The prompt told the model its own
  document_id and asked it to echo it back in `source_refs`. It didn't
  reliably comply. Fix: stop asking the model to echo data the code
  already has -- overwrite `document_id` deterministically after parsing,
  every time, rather than trusting compliance with a formatting
  instruction.
- **Same pattern, different field.** The model sometimes copied the whole
  `"[SOURCE paragraph 1]"` marker into `location` instead of just
  `"paragraph 1"`, despite explicit prompt instructions not to. Fixed with
  both a clearer prompt *and* defensive stripping in code -- the lesson
  generalized from the bug above: don't rely on prompt compliance alone
  for anything code can normalize deterministically instead.
- **`max_tokens=8000` was too low.** A real (larger) document hit the cap
  with `output_tokens == max_tokens` and empty response text -- the model
  spent its entire budget on internal reasoning before writing an answer.
  Raised to 16000, and the error path was made specific
  (`finish_reason == "length"` now gets "truncated at the token limit",
  not a generic "no content" message) so this failure mode is
  self-diagnosing next time instead of requiring a repeat investigation.

### UI debugging: each "it's broken" had a different real cause

A sequence of user reports ("UI is broken", "same error", "failed to
create process") could have been treated as one fuzzy "UI is flaky" issue.
Each was instead traced to a specific, different, fixable cause:

1. **Dead-end error state.** A process detail page for a since-restarted
   (and therefore gone) process collapsed to a bare error paragraph with
   no navigation back. Root cause was really "the in-memory store got
   wiped by a restart I did," but the actual bug worth fixing was that the
   UI had no way out of an error state at all. Fixed both: communicated
   the restart/data-loss reality *and* gave every error state a way home.
2. **"Same error" after the fix.** Turned out to be the browser's own
   address-bar autocomplete landing on `/processes/` (no id), which
   matched no route and rendered nothing. Confirmed via Vite's dev-server
   console forwarding, not guessed. Fixed by adding a catch-all route and
   making the header a link home -- so *any* bad URL has a recovery path,
   not just this specific one.
3. **"Failed to create process."** A CORS mismatch: Vite had just been
   repinned from `localhost` to `127.0.0.1` to fix an IPv6-only binding
   issue, but the backend's CORS allowlist still only had `localhost:3000`
   -- browsers treat those as different origins even on the same machine.
   The fix that caused this bug was itself correct; the fix was
   incomplete, not wrong. Allowed both origins.

Also worth recording: my own browser automation tool turned out to be
network-isolated from these dev servers (couldn't reach `127.0.0.1` at
all, on either the frontend or backend). Recognized as a tooling
limitation and stopped retrying blindly rather than burning turns on it --
the fix was found through server logs and `curl` instead.

### Epic 1 remainder (Visio, images, real merge)

- **Visio:** shape text and `<Connect>` topology are extracted
  deterministically from the `.vsdx` zip's XML, then fed into the *same*
  LLM structuring call already used for text documents (framed as
  `"Connector: 'X' -> 'Y'"` text blocks) rather than building a second,
  separate deterministic-flow-construction path. Reuse over a new
  abstraction, since the existing path was already proven.
- **Images:** no deterministic pre-extraction exists for a photo, so this
  goes straight to vision. Verified against a real generated flowchart
  image (not a mock) -- correctly identified all steps and both
  connecting arrows.
- **Multi-document merge:** real conflict-aware matching (label similarity
  via `difflib` + actor agreement), not the naive append it started as.
  The specific rule worth remembering: a similar-label match with
  *disagreeing* actors is kept as two separate elements, both downgraded
  to `confidence: "low"` -- not silently merged, not silently discarded.
  Per the skill's explicit instruction to surface conflicts for human
  review rather than resolve them automatically either way.

### Epic 2 (Postgres/pgvector): the point where "data wiped on restart" finally got fixed

**Scope decision:** only process/document/schema/embeddings moved to real
persistence. Draft BPMN, chat, versions, and the blueprint overlay
deliberately stayed in the in-memory scaffold, because those epics weren't
built yet -- persisting placeholder data for features that don't exist
would have been speculative, not useful.

**Real bug: ID collision, caught by a real primary key.** LLM-generated
element/actor ids like `"el-1"`/`"actor-1"` are only unique *within one
extraction call*. Two different documents could each produce `"el-1"` for
completely unrelated things -- silently wrong in the in-memory version
(nothing enforced uniqueness), and only surfaced once a real Postgres
`PRIMARY KEY` rejected the collision outright. Fixed at the root, in
`app/ingestion/structuring.py`: every schema gets globally-unique ids the
moment it's created, before it ever reaches the merge logic or the
database -- not patched at the merge or storage layer, which would have
left the same landmine for the next caller.

**Real bug: a background-task commit-timing race.** FastAPI's
`BackgroundTasks` run *before* the request's own DB session commits
(session teardown happens after the response, which includes background
tasks). The ingestion pipeline deliberately opens its *own* DB session for
background work (a separate, correct design decision, documented in
`app/ingestion/pipeline.py`) -- but that session couldn't see the document
row the request had just "added," because it was still sitting
uncommitted on a different connection. Fixed with an explicit `db.commit()`
before scheduling the background task, not by changing the
independent-session design (which was right for other reasons).

**Two Alembic autogenerate gaps**, both caught only by actually *running*
the generated migration, not by reading it: a missing
`CREATE EXTENSION IF NOT EXISTS vector` (autogenerate never adds this) and
a missing `import pgvector.sqlalchemy` (referenced in the generated code,
never imported). Both are known, recurring pgvector-with-Alembic gaps
worth checking for on every future migration that touches a vector column.

**Test isolation:** a dedicated test database with table truncation
between tests, not a wrapping transaction rolled back after each test.
The wrapping-transaction pattern is the more common default, but it would
have been wrong here specifically because the background pipeline opens
its own connection -- a transaction held open on the test's connection
alone would have been invisible to it.

**Proof, not assertion.** The actual verification for "does this persist"
was: create real data, kill the backend process, start a genuinely fresh
one, query it back. Confirmed, not assumed.

### Epic 3 (BPMN generation): the schema was already extracted, so generation needs no LLM call

The single biggest leverage point of this epic: since Epic 1/2 already
produces the full structured `ProcessSchema`, turning it into BPMN XML is
a *pure, deterministic transformation* -- no new LLM call, which makes the
whole generator free, fast, and fully unit-testable without mocking
anything. This was a direct payoff of how the earlier epics were
designed, recognized and exploited rather than reflexively adding another
LLM call because "generation" sounds like an LLM task.

**A wrong-first-instinct, corrected by writing a test, not by review.**
The first version of `POST /bpmn/generate` hard-failed (500) on *any*
structural validation issue in the generated diagram. Writing a test with
a deliberately disconnected element exposed why that was the wrong call:
an orphan node coming from real (imperfectly merged) extracted data isn't
a bug in the generator -- it's honest data messiness -- and blocking the
user from seeing *any* diagram over one disconnected node is worse than
showing the diagram with the issue flagged. Redesigned: `validation_issues`
ride along in the response instead of blocking generation. `PUT` (a manual
edit) and finalize stayed strict on purpose -- those are deliberate human
actions, where failing fast with clear feedback is the right call, unlike
an automatic draft derived from upstream data the user didn't directly
author.

**Real bug: a diff scanning the wrong scope.** Epic 6's version-diff logic
scanned the *entire* XML document for elements with an `id` attribute --
which also matches DI shape ids (`Shape_Task_c`), a different id
namespace entirely. Invisible until real BPMN with real Diagram
Interchange existed to expose it; the tests that predated this epic used
placeholder non-BPMN XML with no DI section at all, so the bug had nothing
to trigger it. Fixed by scoping the scan to `<bpmn:process>` only.

**Live verification:** generated BPMN from data that had actually been
extracted earlier in this same session (a real 2-step process from a real
uploaded document) and confirmed it survives a real backend restart.

### Pattern worth naming explicitly

Nearly every non-trivial bug this session was found by *running the code
against something real* -- a live API, a live database, a live browser --
not by reasoning about it or trusting that passing mocked tests meant the
system worked. The `max_tokens` truncation, the `document_id` trust issue,
the ID collision, the commit-timing race, the DI-scope diff bug, and the
orphan-node validation design were all invisible until something real
exercised them. Where this session worked well, it was because testing
against reality happened *before* declaring something done, not after a
user reported it. Worth protecting as a working practice, not just a
one-off habit.
