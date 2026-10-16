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

## 2026-09-18 -- Epic 4, Process Diagram UI

### Canvas engine: bpmn-js's full `Modeler`, not a custom canvas

US4.1 (view), US4.2 (direct-manipulation edit), and US4.6 (undo/redo) were
scoped as three separate user stories, but bpmn-js's `Modeler` build
(`bpmn-js/lib/Modeler`) provides pan/zoom/select, drag/resize/connect
editing, and a command-stack-backed undo/redo all as one library, built on
the same DI-mandatory BPMN 2.0 XML the backend already guarantees
(`bpmn-authoring` skill). The alternative -- a bespoke SVG canvas, or a
generic (non-BPMN) diagramming library -- would have meant reimplementing
BPMN-specific editing semantics (palette, connection rules, lane handling)
that bpmn-js already gets right. Chosen deliberately for that reason, not
just because US4.1 names it. See `frontend/src/components/BpmnCanvas.tsx`.

### No metadata in the BPMN XML -> client-side join by stable ID prefix

US4.3 (node/edge detail panel) needs actor/inputs/outputs/source-refs per
element, but `backend/app/bpmn/builder.py` only ever emits `id`/`name` on
each node -- no `<bpmn:extensionElements>`, no custom namespaced
attributes carrying the source schema element id. Rather than changing the
backend to embed metadata in the XML (which would need a corresponding
change to `bpmn-authoring`'s ID/DI conventions and to validation), the
frontend recovers the join itself: `mapping.py`'s existing ID convention
(`Task_<schema-id>`, `Flow_<schema-id>`, `Lane_<schema-id>`) is stable and
prefix-stripped client-side (`ElementDetailPanel.tsx`'s `classifyBpmnId`)
against the already-fetched `ProcessSchema`. This keeps the BPMN XML a
pure spec-conformant artifact and avoids a backend change whose blast
radius would hit Epic 3's already-implemented generation/validation code
for a UI-only need.

### Dirty-state tracking deliberately decoupled from `commandStack.canUndo()`

The obvious way to track "unsaved changes" is `commandStack.canUndo()` --
but that stays `true` even immediately after a successful Save, since the
undo stack itself isn't cleared by persisting. Using it directly would
have shown "unsaved changes" right after a save that just succeeded.
Instead, `BpmnCanvas`'s `commandStack.changed` handler always reports
`dirty=true` (something changed since the last import), and `DiagramPage`
owns resetting `dirty=false` explicitly after both a successful `PUT
/bpmn` save and a fresh `importXML` -- two independent triggers for
"clean," neither derived from the undo stack's internal position.

### Frontend test tooling: bootstrapped from scratch, confirmed with user first

No test runner existed anywhere in `frontend/` before this epic (no
vitest/jest, no RTL, no config) -- confirmed by exploration before
building. Rather than assume either "skip tests for this pass" or "add
full coverage," this was posed as an explicit choice
(`AskUserQuestion`); the user chose to bootstrap `vitest` + React Testing
Library now rather than defer it, so Epic 4's new components ship with
tests from the start instead of being the second frontend feature in a
row with none.

### Environment note: Chrome browser automation can't reach the local dev stack here

Attempted a live click-through of the new diagram page via the
`claude-in-chrome` browser tools against the already-running dev server
(`http://127.0.0.1:3000`, confirmed reachable via `curl` from the shell in
this same session). The automated browser tab could reach the open
internet (`https://example.com` loaded fine) but got "Frame ... showing
error page" for every `127.0.0.1:3000`/`localhost:3000` attempt --
apparently the browser extension runs in a different network context than
this shell/session. Not a code defect; verification for this epic ended up
being done by the user manually in their own browser instead. Worth
knowing before assuming browser-tool verification against `localhost` will
work unmodified in a future session in this environment.

## 2026-09-18 -- Epic 5, Conversational Diagram Editing

### Chat edits mutate the `ProcessSchema`, not the live BPMN XML directly

Two ways to apply a chat-confirmed diff existed: patch the current draft's
XML in place (preserves anything about the XML the schema doesn't know
about, e.g. a manually-added canvas-only node from Epic 4), or apply the
diff to the `ProcessSchema` and fully regenerate XML via the existing
`build_bpmn_xml` -- the same deterministic path `POST /generate` already
uses. Chose the schema path: the `bpmn-chat-ops` skill's diff shape is
explicitly framed in canonical-schema terms (`ProcessElement`/`ProcessFlow`),
and reusing one generation path keeps `validate_bpmn` meaningful without a
second XML-patching engine that could drift from it. Accepted cost: a node
added only via direct canvas manipulation (no schema backing) can't be
referenced by a chat instruction and is invisible to this path -- consistent
with Epic 4's existing "manually added -- no extracted metadata" UI case,
not a new gap.

### Layout: preserve positions by default, explicit "Refresh Layout" to relayout

Regenerating from the schema on every chat edit would auto-relayout the
*entire* diagram each time, discarding any position the user had left a
node at. Raised as a tradeoff and the user's steer was to keep manual
layout by default and offer an explicit escape hatch: `compute_layout`
(`app/bpmn/layout.py`) now takes a `preferred_positions` map, and chat-apply
extracts it from the current draft's own DI (`extract_node_positions`)
before rebuilding, so only genuinely new nodes get auto-placed. A new
**Refresh Layout** button on `DiagramPage` calls the existing, unmodified
`POST /bpmn/generate` (no positions passed) to force a full relayout on
demand. Verified live: after a chat-applied `add_node`, the new node's
auto-placed position collided with a preserved node's (BFS rank-based
placement doesn't know about positions it didn't compute) -- Refresh
Layout resolved it cleanly. This overlap case is accepted, not fixed
further, since the escape hatch exists precisely for it.

### Diff element/flow ids: bare schema ids in the LLM's reasoning, translated to rendered BPMN ids at the API boundary

The LLM reasons and emits diffs using the bare schema ids already in the
`ProcessSchema` context it's given (`el-3`, `f-4`) rather than BPMN node
ids (`Task_el-3`) -- deriving the right prefix depends on element *type*
mapping (`app/bpmn/mapping.py`) that only the backend should own, not
something to ask an LLM to get right. `app/chat/service.py` translates
`target_element_ids`/operation ids to rendered BPMN ids before persisting,
so what's stored/returned already matches what `BpmnCanvas` can highlight,
matching the `bpmn-chat-ops` skill's stated reason for using BPMN ids
(canvas highlighting). `apply_diagram_diff` (`app/bpmn/chat_ops.py`)
accepts either form via `strip_bpmn_id`, since it has to handle a
just-generated diff (bare ids) and a stored/replayed one (rendered ids)
alike.

### Real defect: same-diff temporary ids needed for `add_node`

First real LLM call (Qwen3.7 Flash) for "add a step between X and Y"
produced an `add_element` op plus two `add_flow` ops wiring the new node
in -- but with `"to": null`/`"from": null`, because the new element has no
id until the backend mints one at apply time, and the LLM had no way to
reference "the node I'm adding in this same message." Invisible from
reading the code or from the mocked test suite (which only ever exercised
diffs referencing *existing* ids); only showed up once a real model
proposed a real multi-op diff. Fixed by letting the LLM optionally give an
`add_element` payload a throwaway string id (e.g. `"new-1"`) that later ops
in the *same* diff can reference in `from`/`to`; `apply_diagram_diff` does
a first pass minting the real id for every such temp id before applying
any operation, so ordering within the diff doesn't matter. Documented in
the system prompt with a worked example after this was found, not before --
the first prompt version didn't mention the mechanism because the need
wasn't obvious until a real model hit it.

### Real defect: chat-apply was blocking on pre-existing, unrelated validation issues -- twice, in two different ways

`POST /bpmn/generate` deliberately does not block on `validate_bpmn`
issues from imperfect extraction (see the Epic 3 entry above) -- but the
first version of chat-apply blocked on *any* issue in the post-edit
diagram, full stop. Against a real seeded process whose generated draft
already had two pre-existing issues (no start/end events extracted, so the
first/last task legitimately has no incoming/outgoing flow), this made
every chat edit on that process fail with "invalid diagram," including
edits (like a rename) that had nothing to do with those nodes. First fix:
compute `validate_bpmn` on the current draft *before* the edit too, and
only block on issue *strings* present after the edit but not before.

That first fix was still too strict, found by the user testing a second
real case: appending a node after the process's last step (also with no
explicit end event) moves the "no outgoing flow" complaint from the old
last node (now fixed -- it has an outgoing flow to the new node) to the
new last node (which doesn't have one yet either). Same total problem,
but a different node id embedded in the message text, so a strict
set-difference on issue strings saw it as "a new problem" and blocked a
genuinely net-neutral edit. Redesigned again: compare issue *counts*
before/after, not string identity -- block only if the edit leaves
strictly more issues than it found. A chat edit must not make things
worse (by count), but doesn't have to spontaneously fix pre-existing
problems, and shouldn't be penalized for a problem legitimately moving
from one node to another. Verified live both times against the real
process that exposed each version of the bug.

### Chat-apply error messages: raw BPMN ids swapped for element labels

The 400 response for a blocked chat-apply was surfacing `validate_bpmn`'s
issue strings verbatim (e.g. `'Task_el_8f055fa669de' (userTask) has no
outgoing flow...`) -- meaningful to a developer, not to the Process
Analyst who has to decide what to do next. The user flagged this directly
after hitting it live. `app/bpmn/chat_ops.humanize_validation_issues`
regex-extracts each quoted BPMN id from an issue string and replaces it
with the element's label (or, for a flow id, "the connection from X to
Y") via the same `strip_bpmn_id` join already used elsewhere in this
epic -- applied only at the chat-apply error boundary, not inside
`validate_bpmn` itself, since `PUT /bpmn`'s manual-edit path has no
`ProcessSchema` to label against and validation issues elsewhere in the
app (e.g. `BPMNDocument.validation_issues`) are developer/diagnostic
surfaces, not conversational ones.

### Chat-edit prompt: explicit reconnection rules for `add_node`, after a real bad diff

The same live "append after the last step" test also exposed the LLM
constructing a genuinely wrong diff on the first prompt version: it
removed *two* unrelated flows and rewired one backwards, disconnecting
three nodes, when the correct diff for that case is a single `add_flow`
(the anchor node had no outgoing flow to remove in the first place). The
system prompt didn't previously spell out *how* to work out an add_node's
reconnection -- it only said an add_flow was needed. Added explicit
per-case rules (anchor-has-outgoing vs anchor-has-no-outgoing vs inserting
before an anchor) plus a direction sanity check ("from" happens before
"to"). Re-tested the identical live instruction after the prompt change
and got the minimal, correct one-`add_flow` diff. Not a guarantee against
future bad diffs from other phrasings -- the `apply_diagram_diff` +
`validate_bpmn` safety net (never applies without validating) is what
actually protects the diagram either way; the prompt change just reduces
how often a good instruction produces a diff that trips that net.

### Real defect: `chat_edit` LLM calls needed the same `max_tokens` headroom as document extraction

Same root cause `structuring.py` already documents for `document_extraction`
-- Qwen3.7 Flash spends part of its output budget on internal reasoning
before the visible JSON reply. The initial `max_tokens=4000` for the new
`chat_edit` operation hit that cap on a real call (`finish_reason="length"`,
empty `result.text`, confirmed via `.data/llm_usage.jsonl` showing
`output_tokens: 4000` exactly). Raised to 16000, matching
`document_extraction`'s established budget.

### Chat messages promoted from the in-memory store to real DB persistence

`app/db/models.py`/`app/store.py` had already earmarked this move for
"once Epic 5 is real," mirroring `BPMNDraftModel`'s promotion for Epic 3 --
new `ChatMessageModel`/`chat_messages` table, migration
`027a54473dd3_add_chat_messages_table`. Necessary for US5.5's audit trail
to actually survive a restart, which an in-memory store can't do.

### Environment note: `uvicorn --reload` (WatchFiles) proved unreliable here

Across this session, `WatchFiles detected changes... Reloading...` fired
once and then silently stopped picking up further edits to other files in
the same `backend/` tree -- confirmed by a code change (the `max_tokens`
fix above) having zero effect on live behavior until the server was fully
stopped and restarted. Root cause not diagnosed (Windows-specific
file-watcher flakiness is a known category of issue for this tool, but
wasn't confirmed further). Practical takeaway for future sessions: after
more than one or two backend edits, do a full `TaskStop` + restart and
confirm `Application startup complete` in the log before trusting live
manual verification against the dev server, rather than assuming
`--reload` picked up the latest change.

## 2026-09-18 -- BPMN swim-lane layout, Epic 10 logging seed

User uploaded a real, complex HR Onboarding document (5-7 actors,
18-20 steps) specifically to stress-test the diagram generator beyond the
small examples used so far. It surfaced three real, previously-invisible
defects -- none caught by the existing mocked test suite, all found by
actually generating a diagram from real extracted data.

### Real defect: swim lanes never rendered at all

Reported as "the diagram is just one flow of various nodes" despite the
schema correctly extracting 5 distinct actors. Root cause, confirmed by
reading bpmn-js's own import source (not assumed): `BpmnTreeWalker.
handleLane` only draws a lane via `visitIfDi`, a no-op when the DI map has
no entry for that lane's id -- and `app/bpmn/builder.py` never emitted a
`<bpmndi:BPMNShape>` for any `<bpmn:lane>`, only for flow nodes. The
semantic `<bpmn:laneSet>`/`flowNodeRef`s were always complete; the visual
swimlane bands simply never existed. Compounding this, the *node*
Y-position algorithm (`row = index within this rank's node list`) wasn't
lane-consistent either -- a rank with only 2 of 5 lanes present would put
those nodes at rows 0-1, colliding with a *different* rank's unrelated
lanes also at rows 0-1. Fixed with two changes: `compute_layout` now gives
every lane a fixed, non-overlapping Y-band (by lane declaration order,
sized for max concurrent same-lane nodes per rank) so a lane's Y no longer
depends on what else is happening at a given rank; `compute_lane_bounds`
(new) computes each lane's own DI bounds from its members' actual
positions, and `builder.py` emits a `BPMNShape` per lane. `validate_bpmn`
gained a matching check (a lane's DI shape is now part of "every semantic
element needs DI and vice versa," not just flow nodes) so this class of
regression fails loudly instead of silently degrading. Knowledge captured
in the `bpmn-authoring` skill for future sessions, not just this fix.

### Real defect: an undeclared actor/element reference crashed ingestion outright

A second HR document failed to ingest at all: `structuring.py`'s id-remap
step used `actor_remap.get(element.actor_id, element.actor_id)` --
falling back to the *original* (never-remapped) local id when the LLM
referenced an actor id it never actually declared in `actors`. That stale
local id then hit `process_elements.actor_id`'s FK constraint at persist
time. Fixed by dropping the fallback (`actor_remap.get(element.actor_id)`
-> `None`, which `actor_id`'s optionality already supports cleanly) for
elements, and -- since `ProcessFlow.from_`/`to` are required, non-nullable
fields, so the same trick isn't available -- by dropping any flow whose
endpoints still reference an undeclared element after remapping, rather
than let it reach the DB. Re-uploading the same real file after the fix
succeeded end-to-end (7 actors, 18 elements, 20 flows).

### Real defect: a genuine rework loop hung the entire API process

The same document's extracted flow graph contained an actual cycle -- "Confirm
start clearance" could flow to "Deferral of start if checks incomplete"
and back to "Confirm start clearance" again, a legitimate rework/retry
pattern in a real business process, not malformed extraction output.
`layout.py`'s rank computation was an *unbounded* longest-path relaxation
(re-queue a node whenever a longer path to it is found) -- sound for a DAG,
but a cycle keeps producing "longer paths" forever. Generating this
diagram hung the single-threaded FastAPI process indefinitely (confirmed:
even `/healthz`, with zero dependencies, stopped responding). Diagnosed by
writing a small offline script against the actual persisted schema to
detect the cycle directly, rather than guessing from the hang alone.

First fix was a hard iteration bound (Bellman-Ford's standard termination
count for an acyclic graph) -- stops the hang, but a *test* for the
follow-up edge-routing fix below exposed that an arbitrary cutoff mid
-relaxation can leave a cyclic component's nodes in an unstable relative
order (which of two nodes in a 2-cycle ends up with the higher rank number
depends on exactly where the bound happens to land), which then fed wrong
"is this edge forward or backward" decisions downstream. Replaced with
`_rank_nodes`: proper Kahn's-algorithm topological ranking (a node is only
finalized once every predecessor already has a rank, giving exact longest
-path semantics with no revisits needed) for the DAG portion, falling back
to a plain single-visit BFS -- never revisits a rank once set, so it can't
oscillate -- for whatever's left unranked, which is exactly the nodes in or
only reachable via a cycle. No arbitrary bound needed at all; termination
is structural (each node touched a fixed number of times), not a cutoff.
Regression tests run the layout/build functions on a worker thread with a
wall-clock timeout (no `pytest-timeout` dependency available) so a future
regression here fails a test instead of hanging the suite.

### Real defect: diagonal edges read as visual noise across real swimlanes

User's words, after checking the rendered HR Onboarding diagram: "wiring
is [messy] and over each other." Root cause: `builder.py` drew every
`sequenceFlow` as a straight 2-point line from the source's right-center to
the target's left-center, regardless of how far apart they were. That's
invisible-ish in a small same-lane chain, but once real swimlanes exist
(the earlier fix in this session) a straight line between two nodes in
*different* lanes cuts diagonally across every lane band and unrelated
node in between -- which is also not how any real BPMN tool draws a
sequence flow; they all use orthogonal (Manhattan-style) routing. Worse,
a backward flow (the rework loop from the previous entry) got the exact
same straight-line treatment, drawing a line running backward through
everything between source and target.

Fixed with `compute_edge_waypoints` (`app/bpmn/layout.py`), three cases:
same-lane forward stays a single straight horizontal segment (no need to
bend what's already clean); different-lane forward routes as
right-vertical-left through the midpoint of the gap between the two rank
columns (a lane-agnostic gap where no node ever sits, so this doesn't
guarantee zero crossings for a flow that skips multiple ranks, but
replaces a diagonal cutting through everything with two axis-aligned
bends); backward (`target.left < source.right` -- covers both a genuine
loop-back and same-rank edges) routes as a loop below the *entire*
diagram's lowest point, not just these two nodes, matching how real BPMN
tools draw a rework loop. Verified live: the real "Confirm start
clearance" <-> "Deferral" loop now draws down-across-up below all 7 lanes
instead of a nonsensical backward diagonal.

### Epic 10: seeded persistent app logging

`app/main.py` only ever called `logging.basicConfig(level=logging.INFO)`
-- console output that doesn't survive past the terminal it ran in. Added
`APP_LOG_ENABLED`/`APP_LOG_PATH` (mirroring the existing
`LLM_USAGE_LOG_ENABLED`/`LLM_USAGE_LOG_PATH` pattern) and a `FileHandler`
alongside the console one. Deliberately kept distinct from the LLM usage
ledger (`llm_usage.jsonl`) -- that's a structured cost record, this is
general request/error/lifecycle logging. Minimal seed of Epic 10's
"Logging" placeholder, not a full structured-logging or log-rotation
solution.

## 2026-09-18 -- Epic 6, Diagram Finalization & Versioning

### Finalized versions promoted from the in-memory store to real persistence

Same shape as Epic 5's chat-message promotion: `app/api/versions.py`'s
four endpoints (finalize/list/diff/restore) were already fully built and
tested against `app/store.py`'s in-memory store, which meant a finalized
baseline -- the thing Epic 7's blueprint generation is supposed to trace
back to -- was silently lost on every backend restart. New `VersionModel`
(`app/db/models.py`) + `app/db/repository.py` CRUD, with the route logic
itself unchanged. Verified live, not just via the (unchanged) test suite:
finalized two versions against a running backend, restarted the process
mid-session, and confirmed `GET /versions` still returned both -- the
failure mode this promotion fixes is invisible from reading the code or
from tests that never restart the process between assertions.

### Restore stays XML-only, on purpose -- not extended to also snapshot the schema

Considered also snapshotting `ProcessSchema` at finalize time so restore
could put the schema tables back in sync with the restored XML, not just
the draft BPMN row. Rejected: `PUT /bpmn` (manual canvas edits, Epic 4)
already lets the draft XML diverge from `process_elements`/`process_flows`
on purpose -- restoring a schema snapshot on top of that would silently
overwrite a manual edit that was never supposed to touch the schema in the
first place. Restore stays exactly what US6.2 asked for ("restore an
earlier one" = get that diagram back), and the pre-existing draft/schema
divergence is a boundary this epic didn't introduce and shouldn't try to
paper over as a side effect.

### `VersionDiffResult` gained a `labels` map for the frontend diff view

`GET /versions/diff` only ever returned raw BPMN ids (`"Task_c"`) in its
added/removed/changed lists -- fine for the existing id-only tests, but
Epic 6's new `VersionsPage` needed something human-readable for a diff a
Process Analyst is meant to read. Added `labels: dict[str, str | None]`
(id -> element name, "to" version winning over "from") as a purely
additive field rather than reshaping the existing three lists, so no
existing caller/test needed to change.

## 2026-09-19 -- Epic 6 follow-up, real Finalize error found via live testing

### Real defect: Finalize's blocked-validation error leaked raw BPMN ids

User clicked Finalize and got: `"Cannot finalize invalid BPMN:
[\"'Task_el_8f055fa669de' (userTask) has no incoming flow...\", ...,
\"Lanes with no DI shape...: ['Lane_actor_1f04adb2251b', ...]\"]"` -- a
Python list repr of raw internal ids, not something a Process Analyst can
act on. Root cause: `app/api/versions.py`'s `finalize_process` built its
400 detail directly from `validate_bpmn`'s raw issue strings, never
routing them through `humanize_validation_issues`
(`app/bpmn/chat_ops.py`) -- the exact helper Epic 5 built for this exact
problem, after this same user hit it in chat-apply and asked "how can we
make such exception explainable to users without using underlying
node_id". That fix only ever landed in `app/api/chat.py`; the sibling
finalize endpoint (US6.3) was never updated to match when it started
using the same `validate_bpmn` checklist. Fixed by resolving the current
process schema and humanizing before raising, same as chat-apply; falls
back to raw issues only if no schema exists yet (a manually-edited draft
via `PUT /bpmn` can have none). Reusing `humanize_validation_issues`
required no changes to it -- it already had a `"lane"` branch, unused
until now since chat-apply diffs never produce lane-shape issues.

Investigated the specific diagram that triggered this (`proc_cd46c74845c7`,
"Persistence Proof" -- a 3-bare-task test process from earlier live
testing, the same one `humanize_validation_issues`'s own docstring example
id came from). Confirmed via `GET /api/processes/{id}` that its schema
genuinely has zero `start_event`/`end_event` elements -- the "no
incoming"/"no outgoing" issues are correct, not a bug. The third issue
("lanes with no DI shape") *was* stale: this draft's `generated_at`
predates the swim-lane DI-shape fix earlier in the session; regenerating
via `POST /bpmn/generate` confirmed it picks up lane shapes now and that
issue disappears, leaving only the two genuine missing-start/end-event
issues -- a real gap in that schema, not something Finalize should paper
over (US6.3's whole point).

## 2026-09-19 -- Epic 7, Agentic Blueprint Generation Engine

### Evaluate against the finalized version's XML, not the live schema

Considered building the blueprint prompt from `ProcessSchema` (elements/
flows), matching Epic 5's chat-ops convention of reasoning in bare schema
ids. Rejected: a finalized version is an immutable XML snapshot (Epic 6)
that can already have drifted from the schema tables -- a manual canvas
edit via `PUT /bpmn` never touches them, and Epic 6's `restore_version` is
deliberately XML-only for the same reason. Evaluating against the schema
instead of the actual finalized XML could score nodes that aren't even in
the diagram being finalized (schema has since moved on) or miss ones that
are. `app/bpmn/nodes.py`'s `extract_flow_nodes` parses the version's XML
directly (id, label, bpmn type, lane, predecessors/successors with flow
conditions), and every result's `node_id` is the real BPMN element id
(`Task_el_4`) -- a second, useful consequence: Epic 8's canvas overlay can
highlight the exact element bpmn-js already renders with no id-translation
layer, unlike chat-ops's bare-schema-id convention (which exists because
that LLM call proposes *new* elements with no BPMN id yet -- a different
problem).

### One LLM call for the whole diagram, not one per node

US7.5's consolidation recommendations ("these three sequential steps
should be one agent") need cross-node context the model can only have if
it sees the whole diagram at once -- per-node calls would need a separate
second pass to reconcile consolidation groupings afterward. Same
prompt-with-JSON-schema-contract pattern as `app/chat/prompts.py`
(`##TOKEN##` substitution, not `.format()`, for the same
literal-braces-in-the-example reason). `max_tokens=16000`, matching
`chat_edit`/`document_extraction` -- Qwen3.7 Flash's reasoning overhead is
now an established, not hypothetical, cost across every structured-output
call site in this project.

### Never trust the LLM's node coverage -- verified live against real automation reasoning

Same principle as `app/ingestion/structuring.py`'s id remapping: the
service drops any result whose `node_id` doesn't match a real node in the
diagram, then hard-fails (`BlueprintServiceError`, -> 502) if any real node
got no result at all, rather than silently completing the overlay with a
placeholder. A blueprint that silently skipped a node would contradict
US7.1 ("every node ... evaluated") in a way a user has no way to notice
from the UI alone. Verified live (not just against the mocked test suite)
with a real Qwen3.7 Flash call against an 8-node expense-reimbursement
diagram with a decision gateway and a rework-adjacent exception branch:
every node covered, correct consolidation (two nodes sharing one
`agent_spec` with `consolidated_from_nodes` on both), the approval step
correctly flagged `not_automatable` with a liability-based reason (not a
generic one), and an exception-handling step correctly scored `partial`
with a `review_before_action` checkpoint -- the rubric held up against a
real model call, not just a hand-written fixture.

### Real gap found (not fixed here): both real HR Onboarding test documents fail Finalize

While picking a real finalized diagram to run the live blueprint check
against, found that both previously-ingested "HR Onboarding" documents
(from Epic 3/6 testing) still fail `POST /bpmn/generate`'s
`validate_bpmn` checklist with "no incoming flow, not a start event" /
"no outgoing flow, not an end event" on multiple nodes -- neither
extracted schema has a `start_event`/`end_event` element at all, only bare
tasks. This blocks Finalize entirely for both, meaning neither can
actually reach US7.1's blueprint evaluation without a chat edit first
adding start/end events. This looks like a real, not-yet-investigated gap
in `app/ingestion/structuring.py`'s extraction prompt for multi-branch,
multi-actor documents (out of scope for Epic 7 to fix) -- worth a
follow-up look at why the LLM omits start/end events specifically on
complex real documents when the schema explicitly supports them. Used a
small hand-built valid diagram for this session's live verification
instead of forcing a fix here.

### In-memory store fully retired

Blueprint overlay was the last thing left in `app/store.py`'s
`InMemoryStore` (versions and chat messages were already promoted in
Epics 5/6). With nothing left to hold, removed `InMemoryStore`,
`ProcessSideData`, `get_store`, and `StoreDep` entirely rather than leave
an empty scaffold around -- `app/store.py` now only defines
`NotFoundError` (still used by `app/db/repository.py`'s lookups and
`app/main.py`'s global exception handler). Also dropped the unused
`new_id`/`utcnow` re-export from that module -- nothing imported them from
there anymore (everything already used `app.ids` directly), a leftover
from before Epic 2/3 promoted the first pieces of data out of the store.

## 2026-09-19 -- Epic 8, Agentic Blueprint Interactive Visualization

### Read-only NavigatedViewer for the blueprint canvas, not the existing editable BpmnCanvas

`DiagramPage`'s `BpmnCanvas` wraps bpmn-js's `Modeler`, which ships a
palette and context pad for editing -- fine for the draft-editing page,
misleading on a page whose only job is to show what the finalized diagram
already locked in (US8.1/US7.6's "non-destructive overlay" point extends
to the UI: nothing here should look editable). Added a second, smaller
component (`BlueprintCanvas.tsx`) built on `bpmn-js/lib/NavigatedViewer`
instead -- pan/zoom/selection only, no editing modules, confirmed via
`node_modules/bpmn-js/lib/Viewer.js` that `SelectionModule` is already
part of the base `Viewer` (so `selection.changed` and click-to-select work
identically to the Modeler-based canvas) without pulling in the
edit-specific modules `NavigatedViewer` doesn't include.

### Overlay coloring via `canvas.addMarker`, not embedding style in the BPMN XML

Verdict color-coding (green/amber/red per node) is applied client-side as
CSS marker classes (`canvas.addMarker(nodeId, "blueprint-node-automatable")`
etc., targeting `.djs-visual > :first-child` the way bpmn-js's own
highlighting examples do) rather than writing color into the finalized
XML. Consistent with the overlay being separate, regenerable data
(US7.6/US7.7) -- the finalized diagram XML must stay exactly what was
locked in, and re-running blueprint generation or an override must not
require re-touching that XML. Markers are re-applied whenever the overlay
changes (regenerate, override) via a `diagramReady` state flag rather than
a ref-based readiness check, since the marker-apply effect needs to
re-fire both when the diagram finishes importing and when `markers` itself
changes -- a plain ref gate (`xml === importedXmlRef.current`) only covers
the first case.

### Agent count dedupes by `consolidated_from_nodes`, not by counting nodes with an `agent_spec`

US8.3's "total number of agents identified" isn't `nodes.filter(n =>
n.agent_spec).length` -- Epic 7's consolidation puts the *same* agent spec
on every node in a consolidated group (each carrying an identical
`consolidated_from_nodes` list), so naively counting nodes would
double-count a two-node consolidated agent as two agents. `BlueprintPage`
instead dedupes on the sorted `consolidated_from_nodes` list (falling back
to the node's own id when that list is empty), giving one agent per
distinct group regardless of how many nodes share it.

### Live-verified the full generate/override/export round trip against a real backend + LLM call

All four existing real processes in the local dev DB (`Persistence
Proof`, both `HR Onboarding` documents) still fail `POST /bpmn/generate`'s
validation with missing start/end events -- the same gap logged under
Epic 7 above, not a new one, and still not itself in scope here. Rather
than block Epic 8 verification on fixing that ingestion gap, hand-built a
minimal valid 4-node BPMN diagram (start event -> two tasks -> end event),
`PUT` it as a draft, finalized it, and drove the actual
`/blueprint/generate` (real Qwen3.7 Flash call, ~47s), `/blueprint`
(GET), `/blueprint/nodes/{id}` (PATCH override), and `/blueprint/export`
endpoints against it -- the exact calls `BlueprintPage` makes. Override
correctly flipped the node's verdict and the very next export reflected
the overridden verdict, confirming the override write path and the export
read path agree. Chrome browser automation (for a true click-through of
the rendered page) wasn't available this session (extension not
connected) -- this real-API verification plus the passing frontend test
suite (`BlueprintPage.test.tsx`, `DiagramPage.test.tsx`) is the fallback
coverage; a follow-up session with the browser extension connected should
still do one visual pass of the overlay coloring and detail panel before
calling Epic 8 fully done. Smoke-test process deleted afterward
(`DELETE /api/processes/{id}`), no residue left in the dev DB.

## 2026-09-19 -- Real Finalize failure on "HR Onboarding 2", extraction-gap pattern confirmed a third time

### Root cause: an inconsistent fan-out, not just a missing start event

User hit Finalize on `proc_51eb5a63bf6e` ("HR Onboarding 2") and got three
"has no incoming flow" errors. Inspected the actual persisted schema
rather than guessing: `"Classify requirements"` (the genuine first step)
had no `start_event` predecessor at all -- consistent with the already-
logged Epic 7 gap (`app/ingestion/structuring.py`'s prompt explicitly says
"omit start/end events rather than guessing", and apparently guesses wrong
on the conservative side often enough that this is now three real
documents in a row). But the other two flagged nodes,
`"Prepare payroll and benefits"` and `"Provision technology and access"`,
revealed a different and more interesting failure: both already had
correct *outgoing* flows into `"Confirm day one readiness"`, and the same
document elsewhere extracted an equivalent 3-way parallel fan-out/fan-in
correctly (`"Welcome and verify arrival"` -> 3 tasks -> `"Enable first
week"`). So the model clearly intended the same pattern here (`"Define
role readiness"` fanning out to 3 parallel tasks) but only emitted the
fan-out edge for one of the three branches, while still emitting all
three fan-in edges. This is an internal-consistency defect in one LLM
response, not an ambiguous-document case where omission-by-design was the
right call -- confirms `_assign_globally_unique_ids`
(`app/ingestion/structuring.py`) has no graph-completeness check at all
today, only a dangling-foreign-key check on flows.

### Fixed live via chat-editing, not a code change

Used the actual running chat-ops feature (Epic 5) to fix the two real
gaps against the live process: one `add_node` message inserted the
missing start event ahead of "Classify requirements" (correctly wired via
its own `add_flow` operation), one `add_flow` message added both missing
parallel-branch edges in a single diff. Regenerated BPMN
(`validation_issues: []`) and finalized successfully
(`ver_07e2bb88ff15`) afterward -- confirms the chat-ops path is a working
recovery mechanism for this class of extraction gap today, independent of
whether extraction itself ever gets hardened.

### Proposed follow-up (not started): a dedicated gap-analysis step, not just a better prompt

Discussed with the user going further than re-tuning the structuring
prompt: real source documents will keep being incomplete/ambiguous no
matter how the prompt is worded, and multiple documents describing the
same process can also disagree with each other (not yet handled anywhere
in `app/ingestion/merge.py`). Proposed direction -- not yet scoped as an
epic, no code written -- is a distinct gap-analysis step that flags
structural gaps (orphan nodes, missing start/end, inconsistent fan-out
like this one) and cross-document conflicts, and asks the user a
concrete question with options (plus an explicit "do nothing") rather
than either silently guessing (today's extraction behavior) or silently
blocking with a raw validator error (today's Finalize behavior). Next
step is to scope this as its own planning epic before any implementation
-- open questions include where in the pipeline it runs (post-ingestion,
pre-finalize, or both) and whether v1 covers single-document structural
gaps only or also cross-document conflicts.

## 2026-09-19 -- Epic 11, Process Gap Analysis & Clarification, implemented

User resolved all five open design questions from the Epic 11 proposal
above (`planning/epics/11-gap-analysis-and-clarification.md`): runs
automatically after ingestion, LLM-based detection for both structural and
cross-document gaps, a dedicated audit table for every question/response,
replaces Finalize's hard content-completeness validation entirely, and a
lighter dedicated pick-an-option UI rather than reusing chat-ops. Full
implementation plan approved via plan mode before any code
(`app/gap_analysis/`, `GapFindingModel`, `app/api/gap_analysis.py`,
`GapReviewPage.tsx`).

### `validate_bpmn` split into structural vs. integrity, not a new function from scratch

`app/bpmn/validation.py`'s content-completeness checks ("no incoming/
outgoing flow") moved to gap analysis's domain; Finalize now only
deterministically checks XML/DI integrity (malformed XML, dangling refs,
missing DI) via a new `validate_bpmn_integrity`. Kept the public
`validate_bpmn` returning the combined list unchanged so its three other
existing call sites (draft advisory display, chat-apply's regression-count
check) and every pre-existing test needed zero changes -- only
`app/api/versions.py`'s finalize_process switched functions.

### Shared `apply_diff_and_persist` extracted from chat-apply, reused by gap-finding resolve

A resolution option's `diff` is exactly a `DiagramDiff` (same shape
chat-ops already produces), so gap-finding resolve applies it through the
identical apply -> rebuild XML -> regression-check -> persist sequence
`app/api/chat.py`'s apply endpoint used inline. Extracted into
`app/bpmn/chat_ops.py::apply_diff_and_persist` rather than duplicated --
correctness-critical logic (the regression check has its own real-bug
history, see that function's docstring) that two call sites needed
identically. `chat.py`'s own tests needed no changes; behavior is
unchanged, just relocated.

### Finalize's `gap_analysis_completed_at is None` gate is a genuinely separate state from "no findings"

"Never checked" and "checked, found nothing" must not collapse into one
state -- a process created before this migration, or one whose ingestion-
time gap-analysis LLM call silently failed (best-effort by design, must
not fail ingestion), would otherwise finalize with zero gap-analysis
coverage and no signal to the user that nothing was actually checked.
`ProcessModel.gap_analysis_completed_at` is set on every successful run
regardless of finding count, checked before the open-findings check.
Live-verified: a real never-analyzed process (`proc_723dad49ec51`, "HR
Onboarding") correctly got "Run gap analysis before finalizing", distinct
from the "N unresolved gap finding(s)" message a checked-but-unresolved
process gets.

### Real defect prevented, not just detected: a genuinely malformed LLM-authored diff was safely rejected

Live-verified against `proc_cd46c74845c7` ("Persistence Proof", the same
bare-3-task diagram from the Epic 7 entry above): the real gap-analysis
LLM call proposed an "add an end event" option whose `add_flow` operation
referenced a temporary id (`"new-end-1"`) that its own `add_element`
operation never actually set (`"id"` field missing from the element
payload) -- exactly the failure mode `app/chat/prompts.py`'s prompt rules
explicitly warn against ("never invent a temporary id and then forget to
use it"), just from a different prompt (`app/gap_analysis/prompts.py`)
reusing the same diff-construction rules. Resolving with that option
correctly 400'd ("'Order Process Complete' (endEvent) has no incoming
flow and is not a start event") via the existing regression-check
machinery in `apply_diff_and_persist`, the finding stayed `open` (not
silently marked resolved), and resolving with the diagram's other,
well-formed option worked cleanly. This is exactly why gap-finding resolve
reuses chat-apply's existing defenses rather than a new, unvalidated apply
path -- an LLM-authored diff is not inherently more trustworthy than a
chat-authored one just because it came from a different prompt.

### Real LLM output quality gaps found, worth a future prompt revisit (not fixed here)

Two things worth flagging, neither a code defect: (1) some findings'
`question` text leaked visible chain-of-thought reasoning ("Wait, let's
look closer... Let's trace the data...") instead of the one-sentence
plain-English question the prompt asks for -- schema-valid JSON, so
nothing broke, just poor UX if shown verbatim in the gap-review UI's
question line. (2) `kind: "cross_document"` fired on a genuinely
single-document process for an element that merely lacked source_refs,
not an actual cross-document conflict -- the taxonomy distinction between
"structural" and "cross_document" isn't being applied precisely when
there's only one document to reason about. Both are prompt-tuning issues
in `app/gap_analysis/prompts.py`, not addressed in this pass; worth a
follow-up if real usage shows this is more than an edge case.

### Live end-to-end verification

Ran the real flow against `proc_cd46c74845c7` with actual Qwen3.7 Flash
calls throughout: `/gap-findings/analyze` found 2 real findings (missing
start event; a genuine logical inconsistency where "Payment verification"'s
output fed nowhere while the warehouse task's input bypassed it entirely
via a direct flow) -- resolved one via its diff, dismissed the other as a
legitimate judgment call. The resolve's automatic re-run (US11.5) then
correctly caught a *new* gap the fix itself introduced (no end event) and
Finalize correctly blocked on it before I'd even tried to finalize. After
resolving/dismissing every subsequent round (including the malformed-diff
case above), `POST /finalize` succeeded (`ver_bcdd96bcd591`). Frontend:
`npx tsc --noEmit` clean, full `npm test -- --run` green (40 tests, 7
files including new `GapReviewPage.test.tsx`); Chrome extension wasn't
connected this session either (same gap noted in the Epic 8 entry above),
so no visual click-through of the actual rendered page -- real-API
verification plus the passing test suite is the fallback coverage again.

## 2026-09-19 -- Epic 11 follow-up, fixed the two LLM output quality gaps

### Told the model the actual document count instead of trusting it to tally source_refs itself

The `cross_document`-on-a-single-document defect (previous entry) was
fixed by computing `len({ref.document_id for element in schema.elements
for ref in element.source_refs})` in `app/gap_analysis/prompts.py`'s new
`_document_count_note` and stating it explicitly in the prompt ("This
process currently has 1 source document(s) ... do not use kind:
cross_document for anything here") rather than just tightening the
existing prose rule further. Same reasoning as giving the LLM the actual
node list instead of trusting it to enumerate a diagram correctly
(blueprint/chat prompts already do this) -- a fact the backend already
knows for certain is a more reliable guardrail than asking the model to
derive it correctly under load. Also explicitly listed "an element with
no source_refs alone" under "do not flag" (it may simply have been
chat-added, which never sets source_refs by design) -- the original
defect's root cause was treating missing source_refs itself as
conflict evidence.

### Made "no reasoning in the question field" an explicit, example-driven rule

The verbose chain-of-thought leakage ("Wait, let's look closer... Let's
trace the data...") wasn't something the original prompt's generic
"specific, plain-English question" instruction was enough to prevent in
practice. Added a concrete rule: reason silently, write down only the
conclusion, never phrases like "wait"/"let me look closer"/"let's trace",
and a length heuristic ("if question runs longer than two sentences, it
almost certainly contains reasoning that doesn't belong there").

### Live-verified both fixes together on a real never-analyzed process

Ran `/gap-findings/analyze` against `proc_723dad49ec51` ("HR Onboarding",
a real single-document process, confirmed via `document_count: 1`) with
the fixed prompt. Both real findings came back as clean 2-3 sentence
plain-English questions with zero reasoning-trace leakage, and both
correctly classified `"structural"` -- no spurious `"cross_document"` on
this single-document process, the exact defect this fix targets. Full
backend suite stayed green (173 tests, prompt-only change, no test needed
updating -- consistent with `app/blueprint/prompts.py` having no
dedicated prompt-text test file either, just service/API-level coverage).

## 2026-09-19 -- Epics 9 & 10, remaining platform foundations / cross-cutting stories

Audited both epics against the actual codebase before starting: most of
Epic 9 (API/frontend scaffold, LLM layer, Postgres, pgvector, local disk
storage, pytest/vitest, .env config) and part of Epic 10 (LLM usage/cost
tracking) were already done as a side effect of building Epics 1-8/11.
Genuinely missing, confirmed by direct inspection (no `Dockerfile`
anywhere, no auth code anywhere, no request-id in logging, no
`ErrorBoundary`): US9.7 (Docker Compose full stack), US9.9/US10.4 (auth +
access control), US10.1 (request-id log correlation), US10.5 (the
data-privacy decision `claude-api-access-notes.md` already
forward-references but never states), and one real gap in an otherwise
largely-met US10.2 (no top-level error boundary). User decisions: a
lightweight named-user auth model now (no password, explicitly not a dead
end for a future real SSO login), simple built Docker containers (not
hot-reload dev containers), all delivered in one pass. Full plan approved
via plan mode before any code.

### Real defect: naive vs. tz-aware datetime comparison crashed session expiry checks

`app/ids.py`'s `utcnow()` returns a tz-**aware** UTC datetime, but every
`DateTime` column in this project (including the new `SessionModel.expires_at`)
is a plain, tz-**naive** column -- Postgres/psycopg round-trips it naive.
Every *other* `utcnow()` call site in `app/db/repository.py` only ever
*assigns* it to a column (fine -- the aware value's UTC wall-clock time is
stored correctly, tzinfo just silently drops), never compares it against a
value read back from the DB. `create_session`/`get_user_for_session` is
the first place that does: `record.expires_at < utcnow()` raised `TypeError:
can't compare offset-naive and offset-aware datetimes` on every single
request past the first, since `record.expires_at` comes back naive from
Postgres while `utcnow()` stays aware. Invisible until the full test suite
actually exercised a real login -> subsequent authenticated request
round-trip (66 of 179 tests failed the first run). Fixed by stripping
tzinfo at both write (`create_session`) and compare
(`get_user_for_session`) time via `.replace(tzinfo=None)`, keeping
`expires_at` consistent with every other naive timestamp column rather
than making this one column special (`DateTime(timezone=True)`) and
risking the same mismatch resurfacing wherever it's compared against
another table's naive column later.

### Process defect: `npx tsc --noEmit -p .` was never actually type-checking anything, all session

While updating test fixtures for the new `created_by`/`decided_by`/
`overridden_by` fields, ran the frontend's "clean type-check" verification
the same way as every prior epic this session (`npx tsc --noEmit -p .`)
and got zero errors -- looked clean. Deliberately re-verified with a
throwaway file containing an obvious type error (`const x: number = "not
a number"`) to sanity-check the checker itself, and it *still* reported
zero errors. Root cause: `frontend/tsconfig.json` is a solution-style
config (`"files": []` + `"references"` to `tsconfig.app.json`/
`tsconfig.node.json`) -- `tsc -p .` on a references-only config does not
traverse into the referenced projects; only `tsc -b` (build mode, what
`package.json`'s own `"build"` script actually uses: `tsc -b && vite
build`) does. `-p . --noEmit` was silently checking nothing all session.

Re-ran with the correct `tsc -b --noEmit` and it immediately found 6 real
errors -- all pre-existing test fixtures from Epic 8/11 (`BlueprintPage
.test.tsx`, `GapReviewPage.test.tsx`, `VersionsPage.test.tsx`) missing the
new attribution fields now required by their types, exactly the kind of
error the "clean type-check" claims in those epics' summaries should have
caught already had the check been real. Fixed all 6 (added the missing
fields to each fixture); `tsc -b --noEmit` now genuinely clean. **Every
prior "type-check: clean" claim this session (Epics 8, 11, and the
gap-analysis prompt fix) was made on this broken invocation** -- the
underlying code was still correct by luck (nothing in those epics'
`request_bodies`/response shapes actually drifted from their types at the
time), but the verification itself was never real. `tsc -b` (or `tsc -b
--noEmit` to skip emitting `.tsbuildinfo`/build artifacts) is the correct
command for this project going forward, not `tsc -p . --noEmit`.

### Live-verified the Docker Compose full stack, and found a real routing gotcha doing it

`docker compose build api web` (~500s, mostly torch/sentence-transformers)
then `docker compose up -d` reported all three containers healthy, but
`curl 127.0.0.1:8000/healthz` and `127.0.0.1:3000/` were actually still
being answered by this session's own long-running native `uvicorn
--reload`/`vite` dev processes, not the new containers -- confirmed by
checking the api container's own log after a curl and seeing no matching
request line. Root cause: the native processes bind `127.0.0.1:8000`/
`127.0.0.1:3000` specifically, while Docker Desktop's port-publish binds
the wildcard `0.0.0.0:8000`/`0.0.0.0:3000` -- Windows socket routing
prefers the more specific interface match, so a request to `127.0.0.1`
always won for the native process regardless of the container being
perfectly healthy. Not a Docker or code bug -- just meant the containers
were silently never receiving traffic during the first verification pass.
Stopped the native dev processes, re-verified: the api container answered
(confirmed via its own request log line), served real data from the
shared `db` (existing processes from this session's testing showed up,
proving migrations ran and it's the same database), a real
login->session-cookie->authenticated-request round trip worked, and the
web container served the actual built static bundle (no `@vite/client`/
`@react-refresh` dev-mode markers) with the correct baked-in API base URL
and a working SPA fallback for a client-side route. Stopped the
containers and restarted the native dev servers afterward -- Docker isn't
meant to replace the day-to-day dev loop (per the "simple built
containers, not hot-reload" choice), so leaving them running would have
left active development pointed at non-reloading containers instead.

## 2026-09-20 -- Axyntro brand pack integration

### Nav bar recolored to Midnight Navy, not left white with blue accents

The frontend was rebranded from the generic "Agentic Solution Generator"
placeholder to Axyntro using the brand pack dropped at
`frontend/brandassets/` (`axyntro_brand_system/AXYNTRO_BRAND_STANDARDS.md`,
`axyntro-tokens.css`/`.json`, `axyntro_logo_package/`). The one real design
call: `AppHeader` (`frontend/src/App.tsx`) went from a plain white bar to a
solid Midnight Navy (`#05072E`) surface with the white-tone logo, rather
than keeping white with blue accents. Reason: the brand doc's s.5 "Colour
proportions" explicitly assigns Midnight Navy/Deep Indigo to "structure,
navigation and hierarchy" (the 30% band) and reserves Agent Blue/Cyan/
Violet for the 10% "interaction and brand energy" band -- a white nav with
blue highlights would have inverted that ratio the brand doc calls out by
name. Primary buttons (Midnight Navy, Agent Blue on hover) and the login
page (Midnight Navy backdrop with two soft cyan/violet glows behind a
white card) follow the same read of s.5/s.8/s.10.

### Consumed the vendor token file directly instead of hand-copying hex values

`axyntro-tokens.css` was copied verbatim into `frontend/src/styles/` and
pulled into `index.css` via `@import`, with the app's existing semantic
names (`--accent`, `--ink`, `--border`, `--surface`, etc.) aliased onto the
`--axyntro-*` tokens rather than redefining their hex values locally. This
follows the brand doc's own s.11 governance rule ("consume the supplied
token names in code; prohibit raw hex values through linting except
inside the token file") -- a retheme now only ever touches the token file
or the one alias block, never the ~40 call sites across the stylesheet.
All hardcoded status colors (badges, the blueprint canvas overlay fills,
stepper/version pills) were swept to the brand's semantic
success/warning/error tokens in the same pass, since they were the only
remaining raw hex values in `index.css`.

### Not live-browser-verified

`tsc -b --noEmit` and the full vitest suite (48 tests) pass after the
change, but the Claude-in-Chrome extension was not connected in this
environment, so the visual result (contrast on the navy header, logo
rendering, login page glow) has not actually been seen rendered -- only
reasoned about from the brand doc and the source PNGs. Should be checked
against the running dev server before treating this as done.

### Follow-up same day: brand system replaced with v2.0, several tokens renamed/removed

The user swapped `frontend/brandassets/axyntro_brand_system/` for a new
"lighter digital system" version a few hours later (same session) -- logo
package files unchanged, only the standards doc and token files. Notable
renames that required a code sweep, not just a token-file refresh:
`--axyntro-midnight` -> `--axyntro-royal-navy` (a visibly lighter navy,
`#05072E` -> `#18285F`), `--axyntro-cloud` split into `--axyntro-page`
plus new `--axyntro-blue-mist`/`--axyntro-violet-mist`, and
`--axyntro-ink` removed entirely (v2's neutral table only defines Royal
Navy for headings and Slate for body/secondary text, no single catch-all
"ink"). Reflected that split in `index.css`'s alias block: `--ink` now
points at `--axyntro-slate` (body text) and a new `--heading` alias points
at `--axyntro-royal-navy`, applied to h1/h2/h3 and the nav bar background.

Two decisions reversed from the v1 pass, both because v2's own spec
changed, not a change of mind: primary buttons went from Midnight-Navy-
with-blue-hover back to **Agent Blue with Button-Blue-hover** (v1 s.8 said
navy primary buttons; v2 s.8 explicitly says Agent Blue primary, Button
Blue #0059D6 hover -- a new token added just for this). And the login
page's dark Midnight-Navy-plus-glow backdrop was replaced with v2's new
"approved light hero gradient" token, which v2 s.2 describes by name as
the default hero/campaign background chosen specifically "without
creating a heavy dark first impression" -- directly superseding the v1
choice to open on a dark screen. Applied the same hero-gradient token to
the home page's `.home-hero` section for the same reason (it was already
named that). The Royal-Navy header itself was kept as-is, just recolored
-- v2 still assigns navy to "headings, navigation and hierarchy" (s.3),
just at a lighter shade and a smaller proportion of the page (30% -> 20%
per s.5, with white/Page-Canvas now 70%). Re-verified with `tsc -b
--noEmit` and the full vitest suite after the sweep -- still 48 passing,
still not live-browser-verified.

## 2026-09-20 -- Epic 12, Agent Artifact Generation

Implemented backend (`AgentArtifactModel` + migration, `app/agents/
generation.py`'s deterministic definition builder, `repository.
generate_agent_artifact`/`list_agent_artifacts`, `app/api/agents.py`) and
frontend (`BlueprintDetailPanel`'s "Generate/Regenerate agent" action,
status badge, download; `BlueprintPage` wiring). See
`planning/epics/12-agent-artifact-generation.md` for scope.

### Generation is deterministic -- no second LLM call

`app/agents/generation.py` maps a blueprint node's already-produced
`AgentSpec` (Epic 7's judgment work) into a system prompt + I/O schema via
plain templating, not a fresh LLM call. The judgment (what the agent
should do) was already made by blueprint evaluation; this epic only
renders that into a runnable format -- the same shape of decision as
`app/bpmn/builder.py` deterministically rendering a `ProcessSchema` into
BPMN XML rather than asking an LLM to write XML. Keeps generation free,
instant, and trivially unit-testable without mocking an LLM.

### Consolidated groups keyed by a deterministic group_key, not the clicked node

US7.5's `consolidated_from_nodes` can appear independently on more than
one node's `agent_spec` in the same group. Without a canonical key,
clicking "Generate" from different member nodes of the same group would
create duplicate artifacts instead of updating one. Fixed by keying
`AgentArtifactModel` on `group_key` (sorted, pipe-joined `node_ids`)
rather than whichever node triggered generation -- verified with
`test_consolidated_group_generates_one_shared_artifact`, which generates
from each of two grouped nodes in turn and asserts the same artifact id
comes back both times.

### Staleness computed on read, not stored as a synced flag

US12.4's staleness check (`repository._is_agent_artifact_stale`) compares
the artifact's stored `source_baseline_version_id`/`source_node_snapshot`
against the process's *current* `BlueprintOverlayModel` on every read,
rather than maintaining a separate mutable status column that could drift
out of sync with reality. Same "derive, don't duplicate and hope it stays
correct" instinct as `BlueprintPage`'s summary stats being computed
client-side rather than persisted. Verified live (see below) against both
a real node override and a real full blueprint regenerate -- both
correctly flip status from `generated` to `stale`.

### Real defect: the running dev server didn't pick up the new route

Live-verifying against the actual dev server (not just pytest against the
test DB) found the new `/blueprint/nodes/{id}/agent-artifact` route
404ing, even though a fresh `python -c "import app.main"` in a new process
showed it correctly registered in `app.openapi()`'s paths. The long-running
native `uvicorn --reload` process (up since the previous day) never
reloaded for the new files (`app/agents/__init__.py`, `app/agents/
generation.py`, `app/api/agents.py`, `app/schemas/agents.py`) or the
`app/main.py` router registration -- root cause not fully pinned down
(WatchFiles is supposed to pick up new files under the watched directory
by default), but a plain restart (kill the process bound to port 8000,
start a fresh `uvicorn --reload`) fixed it immediately. Also found an
unrelated orphaned uvicorn process from a previous session, not bound to
any port -- left alone since it wasn't blocking anything and cleaning up
unrelated stray processes was out of scope here. Lesson: a passing test
suite doesn't guarantee the long-running dev server actually has the code
it's supposed to have; worth an explicit route-registration check (or a
restart) before trusting a live smoke test's result.

### Live end-to-end smoke test, real DB and real LLM calls

After the restart: created a scratch process, PUT a valid BPMN draft, ran
real gap analysis, finalized, ran a real `/blueprint/generate` (produced a
genuine `InvoiceFetcherAgent` spec for a "Fetch invoice" task), generated
its agent artifact (a coherent real system prompt), confirmed
`status: generated`, overrode the node's verdict and confirmed the
artifact flipped to `stale`, regenerated and confirmed it flipped back to
`generated`, then deleted the scratch process. Full backend suite (200
tests) and frontend suite (50 tests) both green throughout.

## 2026-09-20 -- Blueprint page split into three tabs (Blueprint / Agents / Digital Twin Preview)

Frontend-only change, no backend/API changes -- all data (blueprint
overlay, Epic 12's agent artifacts) was already being fetched by
`BlueprintPage`. New shared helper `computeAgentGroups`
(`frontend/src/lib/blueprintLabels.ts`) dedupes automatable/partial nodes
into one entry per consolidated group (US7.5), reused by the existing
summary stat, the new Agents tab's card grid, and the Digital Twin
Preview's chain -- previously that dedup logic was copy-pasted inline in
`BlueprintPage`'s stats `useMemo`.

### Digital Twin Preview's suggestions are a deterministic heuristic, explicitly labeled as such

The third tab suggests additional agents (a process orchestrator once 2+
agents exist; a handoff agent between two *adjacent* agents with no
shared `tools_systems_needed`; a shared exception-handling agent when any
agent's `human_checkpoint` is `escalation_on_exception`) computed
client-side from the current blueprint, not a real simulation. A visible
disclaimer banner states this explicitly and points at Epic 14 (Digital
Twin Simulation & Validation, not yet built) for real scenario-based
validation -- avoids the same fabricated-precision risk flagged in Epic
14's own planning notes (no real baseline timing/error-rate data exists
in this system yet, so nothing here claims to be one).

### Real defect found while writing this feature's tests: `display: none` via a CSS class doesn't hide anything in jsdom

The Blueprint tab's diagram/detail-panel content stays mounted (not
unmounted) when switching tabs, specifically so bpmn-js doesn't have to
re-initialize and `labelsById` stays populated for the other two tabs.
First attempt hid it via a `diagram-body-hidden { display: none }` CSS
class -- worked visually, but a test that selected a node via the new
Agents tab then found the *same* node's rationale/tool text duplicated in
the still-present (just class-hidden) Blueprint-tab panel, since
component tests never load `index.css` at all (it's only imported by
`main.tsx`), so the class-based rule was simply never applied in jsdom and
that content was never actually hidden from `getByText` (which doesn't
filter by visibility regardless). Fixed by hiding it with an inline
`style={{ display: 'none' }}` instead of a class, which jsdom respects
immediately with no stylesheet needed, and scoped the new tabs' own tests
with `within(getByTestId(...))` rather than relying on visibility
filtering at all -- the more robust fix, since `getByText` never checks
visibility even for inline styles.

## 2026-09-20 -- Epic 13, Agent Registry Connectivity

Implemented backend (`app/registry/base.py`'s connector interface,
`app/registry/local.py`'s DB-backed default connector,
`app/registry/manager.py`'s settings-driven connector registry,
`RegistryEntryModel` + migration, `app/api/registries.py`) and frontend
(`RegistriesPage.tsx` for browse/search + health, `RegistryPushAction.tsx`
wired into the Agents tab's detail panel). See
`planning/epics/13-agent-registry-connectivity.md` for scope.

### Connector interface takes no DB session -- the local connector opens its own

`RegistryConnector`'s abstract methods (push/pull/search/check_health)
deliberately don't accept a `Session` parameter, even though the only
implementation today (`LocalRegistryConnector`) is DB-backed. A real
vendor connector would be an HTTP client with no DB session at all --
threading one through the interface just for the local connector's
convenience would leak an implementation detail into every future
connector. `LocalRegistryConnector` opens and closes its own short-lived
session per call instead, the same pattern `app/ingestion/pipeline.py`'s
background tasks already use for the same reason (decoupled lifecycle
from the request that triggered it).

### `get_registries()` is a FastAPI dependency, not a bare cached function

Settings-driven and `@lru_cache`'d like `get_engine()`/`get_llm_client()`,
but exposed as `RegistriesDep` in `app/api/deps.py` specifically so tests
can override it via `app.dependency_overrides` -- the real
`LocalRegistryConnector` has no way to simulate "registry unreachable" or
"credentials rejected" (US13.6), since it's backed by the same trusted
Postgres instance as everything else. Tests inject a `FakeConnector`
(same role as conftest.py's `FakeLLMClient`) to exercise those paths for
real, rather than skipping that coverage.

### Search failures are per-registry, not all-or-nothing

`GET /api/registries/search` returns `{entries, registry_errors}` rather
than either a flat list or a single failure -- one unreachable registry
must not hide results from every other connected registry, and "zero
matches" must never be confused with "the registry search actually
failed." Verified with `test_search_reports_per_registry_errors_without_dropping_other_results`.

### Epic 13's push is deliberately unstatused, not Epic 15's Publish

`RegistryPushAction.tsx` (wired into the Agents tab) calls the same
`/api/registries/{name}/push` endpoint directly, with no draft/generated/
published/deployed status tracking on `AgentArtifactModel` and no
republish-as-new-version semantics -- that richer lifecycle is explicitly
Epic 15's scope (US15.1-15.6). This exists so US13.2's "publish/browse/
search flows can be built and demonstrated end-to-end today" is literally
true rather than aspirational, and so Epic 15 has a real, working
`push()` call to build its lifecycle tracking on top of rather than
building the connector and the lifecycle UI in the same pass.

### Live-verified against the real dev Postgres DB

`GET /api/registries` (local registry reachable/authenticated), a real
push (`Smoke Test Agent`), found it via `GET /api/registries/search?q=
smoke`, confirmed a viewer gets 403 on push, then deleted the smoke-test
row directly (no delete endpoint exists for registry entries -- not
asked for in this epic). Full backend suite (214 tests) and frontend
suite (61 tests) green throughout. Same dev-server-reload gotcha as
Epic 12 recurred (the running `uvicorn --reload` didn't pick up the new
`app/registry/` package or `app/api/registries.py` until restarted) --
now a known, expected step after adding new backend modules, not a
fresh investigation each time.

## 2026-09-21 -- Epic 14 (core slice), Digital Twin Simulation & Validation

Implemented US14.1-14.4 (scenario definition, Proxy/Static system
simulation, auto-only human-checkpoint resolution, tool-call-trace
grading, aggregate results/cost). Manual checkpoints, API system mode, and
US14.5/US14.6 deliberately deferred -- see the "Discovery" and "core
slice" sections of `planning/epics/14-digital-twin-simulation.md` for the
scoping reasoning; not restated here.

### Defect found only by actually running a multi-turn tool-calling loop: `ChatMessage` couldn't represent an assistant's own tool call

`app/llm/types.py`'s `ChatMessage` had `tool_call_id`/`name` for a *reply*
to a tool call, but nothing to attach `tool_calls` to the *assistant*
message that requested them. Invisible until now because every existing
caller (`blueprint/service.py`, `gap_analysis/service.py`) is single-shot:
one completion, read `result.tool_calls`, done -- nothing ever needed to
hand a tool call back to the provider inside a follow-up message. Epic
14's twin loop (`app/twin/engine.py`) is the first caller that actually
continues a tool-calling conversation past one turn, and OpenAI-compatible
APIs reject a `role="tool"` message that isn't preceded by an assistant
message carrying the matching `tool_calls` -- confirmed by triggering the
real error against OpenRouter before fixing it. Fixed by adding
`ChatMessage.tool_calls: list[ToolCall] | None` and serializing it in
`LLMClient._message_to_openai` -- a small, generic addition to the shared
LLM layer (not twin-specific), since any future multi-turn tool-calling
caller would hit the identical gap.

### Human checkpoints modeled as one more callable tool, not special-cased control flow

`app/twin/engine.py` adds a synthetic `request_human_decision` tool to the
same `tools` list passed to `LLMClient.complete()` whenever
`human_checkpoint != "none"`, rather than branching the loop on "is this a
system call or a checkpoint." One code path resolves both a real
(simulated) system call and a simulated human decision, and the trace/
grading logic (US14.3) never needs to know the difference beyond the
`kind` field it already records. The stored `AgentDefinition.system_prompt`
itself is never rewritten for this -- the tool-calling contract is a
twin-only system message appended at run time, so the artifact stays the
portable, deployment-target-agnostic thing Epic 12 produced.

### Live-verified against the real LLM (OpenRouter -> Qwen3.7 Flash), not just the fake client

Ran `schema_inference.infer_tool_schemas` and `engine.run_scenario` for
real against a hand-built `AgentDefinition` (one system, one
`review_before_action` checkpoint) with no mocking. Schema inference
produced a sensible, specific tool schema for a system named only "Order
Management System" (parameters including an inferred `reason_code` enum
it was never told about). The run loop correctly drove a real
`request_human_decision` call, resolved it via the probability config,
then a real system tool call via the inferred schema, then a valid final
JSON output matching the declared output schema -- confirming the
`ChatMessage.tool_calls` fix above actually works against the real
provider, not just the test double.

One real-model behavior worth remembering for later scenario design: the
model bundled the human-checkpoint call *and* the system tool call into
tool_calls of a single turn rather than waiting to see the checkpoint's
result before deciding to act -- i.e. it didn't genuinely wait for
approval before acting, despite the system prompt saying to. The engine
recorded this faithfully in the trace (both steps present, in call order)
and grading correctly flagged it as unexpected when no `expected_steps`
were declared to require the wait. This is a real fidelity limit of
LLM-simulated checkpoints worth surfacing to scenario authors, not a bug
in the twin engine -- `expected_steps` is exactly the mechanism to catch
it when a scenario cares about that ordering.

Full backend suite (234 tests) and frontend suite (62 tests) green
throughout; the new migration (`83365370d5d2_add_digital_twin_tables`)
applied and reversed cleanly against the dev DB.

### US14.5/US14.6: closed out the epic with pure derive/CRUD, no new LLM surface

`TwinBaselineModel` (one row per artifact, replaced wholesale like
`TwinToolSchemaModel`) stores only what an Automation Architect types in
by hand -- typical time and error rate are independent, optional columns,
never inferred. `get_twin_summary`'s comparison math
(`average_run_duration_seconds`, `time_delta_seconds`, `error_rate_delta`)
is computed on read from `TwinRunModel.started_at`/`completed_at` and the
existing pass-rate calculation, not stored -- same "derive, don't
duplicate" choice as `_is_agent_artifact_stale`. A side missing its half
of a comparison (e.g. a baseline with only `error_rate` set) yields `None`
for that one field rather than a fabricated zero.

US14.6 needed no new backend endpoint at all -- `GET .../twin-summary`
already existed from US14.4, so "feed twin results back into the
blueprint view" was purely a frontend wiring task
(`TwinConfidenceBadge.tsx` inside `BlueprintDetailPanel`), read-only and
non-gating per the epic's own notes. Because neither addition touches an
LLM call site, this pass relied on the automated test suite (17 new
backend tests, 2 new frontend tests) rather than a fresh live-LLM pass --
the core slice's live verification already covered the only code paths
that talk to a real model.

## 2026-09-21 -- Epic 15, Agent Publishing & Lifecycle Management

### Version number and "needs republish" computed on read, not stored

`AgentPublicationModel` (new table) is an append-only log of pushes to a
registry, same "kept forever" shape as `VersionModel`/`TwinRunModel` --
US15.4 wants republish to insert a new row, never mutate a prior one.
`version` is deliberately not a stored column: it's the row's 1-based rank
among an artifact's publications ordered by `published_at`, computed in
`repository._list_agent_publication_models`/`_to_pydantic_agent_publication`.
Likewise `needs_republish` isn't a flag -- each publication snapshots
`source_artifact_generated_at` at push time, and it's compared against the
artifact's current `generated_at` on every read. Same "derive, don't
duplicate and hope it stays in sync" choice as `_is_agent_artifact_stale`
(Epic 12) and the twin baseline comparison math (Epic 14).

### Lifecycle status is monotonic across an artifact's whole publish history, not per-version

US15.2 asks for one draft/generated/published/deployed status per agent.
"Deployed" is derived as "any publication for this artifact has ever been
marked deployed", not "the latest publication's own status" -- so marking
v1 deployed and later publishing a not-yet-deployed v2 keeps the
artifact's `lifecycle_status` at "deployed" rather than regressing it to
"published". Verified live against the real dev Postgres DB (not just the
11 new pytest cases): published v1, marked it deployed, regenerated the
artifact (which flips `needs_republish` to `true`), published v2, and
confirmed `lifecycle_status` stayed `"deployed"` with `needs_republish`
correctly back to `false` and both versions present, newest-first, in
`publications`. "Draft" (no artifact generated yet) was deliberately left
out of the backend enum entirely -- there's no `AgentArtifact` row to
compute a publish status for in that state, so it's purely how the
frontend renders `artifact === null`, matching how `AgentArtifactActions`
already handles that case.

### `PublishPanel` absorbed `RegistryPushAction` and `TwinConfidenceBadge` into one component

Epic 13's `RegistryPushAction.tsx` had a docstring pointing at this epic
("the polished, status-tracked, access-controlled Publish action belongs
to Epic 15, built on top of the same endpoint this uses directly") -- it's
now deleted, replaced by `PublishPanel.tsx`. Rather than passing
`agent_name`/`definition`/`tags` from the frontend on every push (the old
component's shape), the new `POST .../agent-artifacts/{id}/publish`
endpoint takes only `{registry_name}` and derives everything else
server-side from the stored artifact and its blueprint node's `step_type`
-- less to keep in sync, and it's what let `repository.publish_agent_artifact`
be the single source of truth `test_publish_tags_the_registry_entry_with_the_node_step_type`
checks. `PublishPanel` also renders `TwinConfidenceBadge` internally
instead of each caller placing it next to the publish button separately
(US15.3: twin evidence "right on the publish action") -- `BlueprintDetailPanel`
and `AgentDetailPanel` (which previously lacked twin evidence entirely)
both now just render one `<PublishPanel>` and get lifecycle status, twin
confidence, and version history together.

## 2026-09-23 -- Per-document process-definition validation

### Gap analysis makes zero LLM calls right after a document's own merge, not one

Added `is_process_definition`/`process_definition_confidence`/`validation_message`
to the structuring LLM contract so a non-process upload (a backlog, a
roadmap, meeting notes) is rejected per-document instead of silently
merging empty/junk extraction results into the process schema
(`app/ingestion/structuring.py`, `structure_process_with_validation` /
`structure_process_from_image_with_validation`).

While verifying this against the real pytest suite (not just reading the
code), a new two-document test assumed three LLM calls would happen:
doc1 extraction, doc1's gap-analysis pass, then doc2 extraction -- and
pre-loaded `fake_llm.complete.side_effect` with three payloads in that
order. It failed with the *second* document silently getting the
gap-analysis stub instead of its intended "this is a backlog" rejection
payload. Root cause: `run_gap_analysis` (`app/gap_analysis/service.py`)
calls `repository.get_process_schema` on the *same* session that just
committed the merge, but for a single freshly-merged document this
returns `None`/empty in a way that short-circuits `analyze_gaps` before
it ever calls the LLM -- confirmed as existing, intentional behavior by
an unrelated, unmodified test in the same file
(`test_pdf_upload_and_extract_process_schema` asserts
`fake_llm.complete.assert_awaited_once()` after a single document with a
non-empty schema). So there are only two LLM calls per two-document
upload, not three; fixed by removing the phantom middle payload from the
new test's `side_effect` list rather than changing production code.

Running the full suite (not just the new test file) after that fix
surfaced a second, unrelated break: `tests/test_db_repository.py` has its
own `_PAYLOAD` fixture, never updated with the new
`is_process_definition` fields, so under the new validation logic it now
defaults to "not a process document" and gets rejected -- the document
ends up `failed` instead of `done`, so the embeddings/schema-change-log
assertions in `test_document_upload_stores_embeddings_and_schema_change_log`
and `test_process_data_survives_a_fresh_session` never see any data.
Fixed by adding the same three validation fields to that fixture. Lesson:
a schema contract change like this one needs a repo-wide grep for every
hand-built LLM payload fixture, not just the test file that motivated the
change.

### Live-testing the deployed feature found two more defects the mocked test suite couldn't catch

The user reported the just-shipped validation feature "not working" against
a real uploaded backlog document. First finding was environmental, not a
code bug: a stray `.venv` `uvicorn --reload` process (started days earlier)
was bound to `127.0.0.1:8000` alongside the Docker `api` container's
`0.0.0.0:8000` -- Windows allows both to coexist, and the browser's
requests to `localhost:8000` were silently landing on the stale process
running pre-feature code. Confirmed by diffing each listener's
`/openapi.json` for the new `process_definition_confidence` field, then
killed the stray process (and its orphaned `--reload` worker subprocess,
which kept holding the port after the parent died).

With traffic actually reaching the rebuilt container, two real defects in
`app/ingestion/structuring.py` showed up that no amount of mocked-payload
testing would have found, only real calls to OpenRouter/Qwen3.7 Flash:

1. **`process_definition_confidence` sometimes returned as a 0-1 fraction**
   (e.g. `0.9`) instead of the prompted 0-100 integer. Pydantic's strict
   `int` field raised `int_from_float`, which surfaced as a generic
   `StructuringError` -- misreported as "not a process document" for a
   document that actually was one (Employee Onboarding, a real test file).
   Fixed by accepting `float` and normalizing (`<=1` treated as a
   fraction, scaled by 100) rather than trusting the model's declared unit.

2. **The model omits `is_process_definition`/`confidence`/`message`
   entirely** (not `null` -- the keys are just absent) in roughly half of
   real calls on a real, larger (24-element) document, confirmed by 8
   repeated live calls on the identical prompt: 4 included the fields, 4
   didn't. The original code defaulted a missing classification straight
   to "reject" -- correct as a last resort, unsafe as the primary
   behavior, since it turned model flakiness into false-negative
   rejections of legitimate documents on a roughly 50/50 coin flip.
   First fix attempt (retry the whole extraction call up to 3x) was
   measured *still insufficient*: on the same 24-element document, 3
   fresh full-extraction retries came back unclassified all 3 times (a
   ~12.5% tail outcome, but real, and each retry cost a full ~15-90s call
   under the host's memory pressure at the time). Redesigned instead as a
   **small, focused classification-only follow-up call** on top of the
   *same* first extraction (keeping its elements rather than discarding
   them) -- cheaper, faster, and a simpler task for the model to actually
   answer, since it isn't competing with a large element-extraction task
   for output budget/attention in the same response.

Verified end to end against the live API after each fix: re-uploaded both
the real backlog document (correctly rejected, with a specific model-
written reason, not the generic fallback) and the real Employee Onboarding
document (correctly accepted, confidence 100, all 22 elements preserved)
through the actual Docker container, not just pytest's mocked client.

## 2026-09-24 -- Document/process deletion (Editor-only), and a real post-deploy login bug

### Only a `failed` document can be deleted, not any document

Added `DELETE /api/processes/{id}/documents/{document_id}` (Editor-only,
matching the existing `DELETE /api/processes/{id}`) so a rejected upload
no longer permanently blocks `Generate draft BPMN` (which requires every
document to reach `done`). Deliberately scoped to `status == "failed"`
only, enforced server-side, not just hidden in the UI: `SourceRefModel.
document_id` has `ondelete="CASCADE"`, so deleting a `done` document would
DB-cascade-delete the `source_refs` rows of any element it contributed to
merge_process_schema's output -- leaving orphaned, provenance-less
elements behind rather than actually retracting the document's
contribution. A failed document never reached `merge_process_schema` in
the first place, so it's the one case that's safe to just remove outright.

### Chased a login redirect loop through two real bugs before finding the actual one

User reported: log in, immediately bounced back to the login screen. Root-
caused via `curl -D -` on the real login response header, not
speculation: `set-cookie: asg_session=...; SameSite=lax` with no `Domain`
attribute, and the frontend's `frontend/.env` hardcoded
`VITE_API_BASE_URL=http://127.0.0.1:8000`. `localhost` and `127.0.0.1` are
different *sites* for SameSite purposes (no shared registrable domain),
so a page opened via `http://localhost:3000` (start.ps1's own printed
URL, and what was suggested for testing) would get a session cookie
scoped to `127.0.0.1` that never gets attached to its own API calls.
Genuine bug, fixed in `frontend/src/api/client.ts`: default
`API_BASE_URL` to `${protocol}//${window.location.hostname}:8000` so the
frontend always calls the API on whatever host the browser actually used,
keeping them same-site regardless of which of the two anyone opens.
Cleared the hardcoded override from `frontend/.env`/`.env.example` so the
dynamic default isn't shadowed.

Rebuilt and reported it fixed -- user replied "same issue still". Rather
than assume the fix was wrong, verified the *deployed* artifact directly:
`curl`'d the live JS bundle out of the running `web` container and
confirmed it now contains `window.location.hostname`, not a hardcoded
`127.0.0.1` -- so the code fix genuinely was live. That ruled out "fix
didn't work" and left "something else". Browser automation
(claude-in-chrome) then hit its own dead end: screenshots failed with a
generic "Frame with ID 0 is showing error page" against a URL `curl`
could reach fine -- looked like a tooling bug, until running
`document.body.innerText` via the JS-execution tool (which, unlike
screenshot capture, still worked) showed the real Chrome error text
verbatim: `ERR_CONNECTION_REFUSED`. That redirected the investigation
away from the automation tool and toward a second, real, separate defect:
`netstat` showed a *second* listener on `[::1]:3000` (IPv6 loopback)
owned by `wslrelay.exe`, distinct from Docker's own `0.0.0.0:3000` /
`[::]:3000` publish -- a stale relay left over from an unrelated,
already-exited `docplatform-web-1` container that had previously used the
same host port. Windows/Chrome's IPv6-preferring resolution of
`localhost` (and, it turned out, even literal `127.0.0.1` inside this
particular automated Chrome instance, which by then was confirmed to be
sandboxed away from local network access entirely -- a tooling
limitation, not a lead worth chasing further) kept landing on the dead
relay. This was ultimately a red herring for the user's *specific*
report, since the user could reproduce the same symptom in a browser
unaffected by that stale relay -- but it's worth knowing this failure
mode exists on this host if `localhost` connections ever mysteriously
refuse elsewhere.

The user's actual root cause, confirmed by them opening the same URL in a
different, cache-empty browser and it working immediately: nginx
(`frontend/nginx.conf`) served `index.html` with only `Last-Modified`/
`ETag` and no explicit `Cache-Control`. Per RFC 7234, a response with no
explicit freshness directive can be treated as heuristically fresh by the
browser for a while, skipping revalidation entirely -- so a browser that
had the SPA open before the rebuild could keep serving the *old*
`index.html`, which references the *previous* build's content-hashed
`/assets/*.js` filenames, indefinitely past redeploys, with no obvious
symptom pointing at caching at all. Fixed by giving `/assets/` (Vite
content-hashes every filename there, so a cached copy is valid forever
under its own name) `Cache-Control: public, max-age=31536000, immutable`,
and everything else -- `index.html` and the SPA fallback -- `Cache-
Control: no-cache`, forcing revalidation on every load. Lesson for next
time: when a just-shipped frontend fix "doesn't work" after a rebuild,
check the *served* Cache-Control headers before re-diagnosing the
application logic again -- an unset header on `index.html` in an nginx-
served Vite SPA is a much more common cause than it looks like from the
symptom alone.

## 2026-09-28 -- Epic 16, Orchestration Rehearsal

Implemented US16.1-16.7: a process-level rehearsal that walks the real
finalized-diagram flow graph (`app/bpmn/nodes.py:extract_flow_nodes`,
reused as-is from Epic 7), running every automatable node's generated
agent via Epic 14's `run_scenario` in sequence, simulating gateways and
non-automatable nodes along the way. See
`planning/epics/16-orchestration-rehearsal.md` for the full design; three
decisions were made up front (not discovered mid-build) and are recorded
there in full -- summarized here only where useful:

### Manual-node default is human-checkpoint simulation, not a bare stub

Most manual steps in a real process are a decision gate, so
`ManualNodeConfig.mode` defaults to reusing Epic 14's approve/reject
simulation (`resolve_human_decision`) rather than inventing a second
simulation concept. `fixed_stub` is the explicit opt-out for a manual step
that's really just a data-producing action.

### Cross-agent data handoff defaults to an LLM adapter call, not a strict field-name contract

Agent I/O schemas are independently generated per blueprint node and won't
reliably share field names -- requiring an exact contract everywhere would
make most rehearsals fail on naming alone before ever exercising real
agent behavior. `exact_field_contract` exists as an explicit per-handoff
opt-in (raises immediately on any missing field, no LLM call) for a pair
of agents an architect has actually locked down.

### Live-verified against the real LLM (OpenRouter -> Qwen3.7 Flash), not just the fake client

`run_scenario`'s own LLM calls were already live-verified by Epic 14; the
one genuinely new LLM call site this epic adds is the `llm_adapter` data-
reshaping call (`app/twin/orchestrator.py:_adapt_data`,
`operation="orchestration_data_adapter"`). Ran a hand-built two-node graph
(start -> task -> end) with a deliberately mismatched field name between
the scenario's start-event input (`customer_identifier`) and the agent's
declared `input_schema` (`customer_id`) against the real API, no mocking.
The adapter correctly reshaped `{"customer_identifier": "CUST-42"}` into
`{"customer_id": "CUST-42"}`, the agent then produced a real summary, and
the run completed `status="passed"` with real cost/token accounting
(~$0.00009, 887 tokens) -- no defect found, first run succeeded.

### Defect found only by rehearsing a real, previously-finalized process: not every diagram has a start/end event

Ran a rehearsal against `HROnboarding` (a real process finalized earlier
in this project, 18 steps, 2 exclusive + 2 parallel gateways) through the
actual UI, not a synthetic fixture. First attempt failed immediately:
"Process diagram has no start event to begin an orchestration run from."
Root cause: `app/api/versions.py`'s Finalize only runs
`validate_bpmn_integrity` (XML well-formedness, dangling refs, duplicate
ids) -- the separate `validate_bpmn` structural check that would flag a
missing start/end event is deliberately *not* a finalize-time block (see
that module's own docstring; Epic 11 owns that gap as an advisory
finding, not a hard stop). `HROnboarding`'s finalized XML legitimately has
zero `<bpmn:startEvent>`/`<bpmn:endEvent>` elements -- just plain
`userTask`/gateway nodes -- and was valid enough to reach Finalize months
before this epic existed. `run_orchestration`'s entry-point/end-of-walk
logic had silently assumed every finalized diagram uses explicit event
nodes, a case none of Epic 7's or this epic's own fixtures (which all use
`_valid_xml()`'s clean start/task/end diagram) happened to exercise.
Fixed in `app/twin/orchestrator.py`: entry point falls back to whichever
node has no incoming flow when no `startEvent` exists (erroring clearly if
that's ambiguous -- zero or multiple candidates); a node with no outgoing
flow is now always a clean end of the walk rather than only when it's
literally tagged `endEvent`. Re-ran against the same real process after
the fix: the walk correctly started, ran four real manual-node
simulations, passed through a single-successor gateway, then correctly
and deliberately rejected the diagram's actual parallel gateway as
unsupported (this epic's own documented scope boundary) -- confirming
both the fix and the pre-existing guard against real, previously-authored
production data, not a synthetic test case. Added regression tests for
the no-explicit-events fallback and the ambiguous-multiple-entry-points
error in `tests/test_orchestrator.py`.

### Persistence deliberately mutates a run row while `status == "running"`

Every other "run" record in this codebase (`TwinRunModel`, `VersionModel`)
is written once and never touched again -- immutable evidence of what
happened. `OrchestrationRunModel` is the one deliberate exception:
`node_runs`/`visited_path` are appended to and committed after every node
while the walk is in progress, specifically so a concurrent `GET` on the
run id shows live progress (US16.7). This reuses the exact
background-task-with-its-own-DB-session pattern `app/ingestion/
pipeline.py` already established for Epic 1's document processing status,
rather than introducing websockets/SSE for what's fundamentally the same
"poll a status field" problem -- confirmed working end-to-end (background
task start -> incremental commits -> polling `GET` reaching a terminal
status) in `tests/test_api_orchestration.py::test_run_starts_and_eventually_completes`,
run against a real Postgres test database, not mocked.

### Groomed the "Digital Twin Agent Simulation" requirements doc into seven follow-on epics rather than reopening Epic 14/16

A separate, much more detailed requirements document describing a full
"Digital Twin Agent Simulation" capability (run lifecycle states, typed
execution steps, three synchronized observability views with replay,
multi-dimensional evaluation, regression suites, version comparison, a
Tool Gateway/Policy Engine enforcing declared permissions and guardrails,
named/versioned twin environments with a broad fault-injection catalog,
execution-config reproducibility snapshots, and a checklist-gated
release lifecycle) was reviewed against the current implementation before
writing anything. Epics 12/14/16 already ship a real, working first slice
of "digital twin" (artifact generation, sandboxed scenario execution with
grading, multi-agent process rehearsal) -- the requirements doc describes
substantially more than that slice, not a different thing entirely, so the
call was to groom it as new, dependent epics (17-23) rather than reopen or
rewrite 12/14/15/16.

Concrete gaps confirmed by reading the actual code, not assumed from the
epic docs' "implemented" status: `app/twin/engine.py` enforces exactly one
runtime limit (`max_turns`) against the requirements' nine-field
guardrails list; `TwinTraceStep`/`TwinRun.status` (`app/schemas/twin.py`)
only distinguish `tool_call`/`human_checkpoint` steps and
`passed/failed/error`, nothing like the requirements' fifteen-value step
taxonomy or `WAITING_FOR_*` states; twin runs execute synchronously inside
one request while orchestration runs already use the background-task/poll
pattern; `AgentArtifact.tools_systems_needed` (Epic 12) is still plain
strings with no permissions, limits, or contract/adapter split attached;
and neither `DigitalTwinPanel.tsx` nor `OrchestrationPanel.tsx` (816 lines
combined) implement anything close to the requirements' three-pane
synchronized-view/replay/inspector UI.

Sequencing decision: Epic 20 (agent definition governance -- permissions,
guardrails, escalation, knowledge versions, tool contracts) first, since
Epic 17's `POLICY_CHECK` step type and Epic 23's validation checklist both
assume its fields exist; Epic 17 (unified run/step model) next, since 18,
19, and 21 all read it; then 18/19/21 roughly in parallel; then 22
(reproducibility snapshots) last, since it snapshots versions the others
introduce; then 23 (quality-gate lifecycle) last of all, since it needs a
real design decision (US23.1) reconciling three lifecycle fields that
would otherwise exist on the same artifact (Epic 12's
`draft/generated/stale`, Epic 15's `draft/generated/published/deployed`,
and the requirements' new `DRAFT...DEPLOYED` chain) -- deliberately not
resolved in the epic itself, flagged as needing its own decision-log entry
once that design pass happens, the same way Epic 14 began as a discovery
spike rather than a committed design.

The requirements doc's Section 12 ("automated agent improvement loop") was
deliberately *not* groomed as its own epic -- the source document itself
frames it as forward-looking ("should support future..."), and Epic 19's
version-comparison/regression-suite stories are exactly the prerequisites
that loop would need, so it's noted as a future revisit in Epic 19's Notes
rather than scoped now.

## 2026-09-29 -- Epic 20, Agent Definition Governance (partial: US20.1/US20.2)

### Governance enforcement is opt-in per artifact, not fail-closed by default

The requirements doc's FR-AG-05 implies permissions should be explicit and
checked, which could be read as "deny any tool call without an explicit
grant." Rejected that as the default: every artifact generated before this
epic, and every existing test fixture across `test_twin_engine.py`/
`test_orchestrator.py`/`test_api_*` that hand-builds an `AgentDefinition`
without the new fields, would have immediately failed its very first tool
call under a fail-closed default -- not a targeted regression, a blanket
one across ~280 pre-existing tests and every already-generated artifact in
any existing deployment. Chose instead: an artifact with an empty
`tool_contracts` list (checked in `app/twin/gateway.py::check_tool_permission`)
falls back to Epic 14's original unrestricted behavior; only once an
artifact actually declares at least one tool contract does permission
checking start applying. `app/agents/generation.py::build_agent_definition`
was updated to *always* populate a tool contract + matching read-only
permission for every tool a blueprint spec names, so every newly generated
artifact opts in automatically -- the opt-out only matters for artifacts
that already existed before this epic shipped, or hand-built test
fixtures that don't care about governance. Confirmed via the full existing
backend suite (283 pre-existing tests) passing unchanged after the schema
and engine.py changes landed, before any new Epic 20 tests were added.

### A denied tool call fails the run but doesn't abort it

Considered raising a hard exception on a denied call (mirroring how
`app/twin/engine.py` already raises `TwinServiceError` for a genuinely
unknown tool name). Rejected: a real unauthorized API call returns a 403
the caller can react to, not a connection reset -- so a policy denial is
fed back to the model as a normal tool observation
(`{"error": "permission_denied", "reason": ...}`), letting the agent
adapt (retry a different approach, escalate, give up cleanly) exactly like
a live system would let it. The denial is still guaranteed visible
regardless of how the agent reacts: `TwinTraceStep.denied` is set, and a
`TwinDeviation` is appended unconditionally, which downgrades an otherwise
grading-correct run from "passed" to "failed" -- so "the agent tried
something outside its permissions" is never silently absorbed into a
passing result even when the agent recovers gracefully.

### Bug found while testing: `BaseModel.model_copy(update=...)` doesn't re-validate

The first version of `repository.update_agent_governance` built the
updated `AgentDefinition` via `definition.model_copy(update=updated_fields)`,
where `updated_fields` came from `AgentGovernanceUpdate.model_dump(exclude_unset=True)`
-- i.e. plain dicts, not `ResourcePermission`/`RuntimeGuardrails` instances.
`model_copy(update=...)` assigns those raw dicts directly without running
them back through pydantic's validators, which surfaced as a
`PydanticSerializationUnexpectedValue` warning the moment the updated
artifact was serialized back out over the API (nested fields were dicts
where `AgentArtifact`'s schema expects model instances). Not caught by
type-checking since `model_copy`'s `update` parameter is typed
permissively; only surfaced by actually exercising the PATCH endpoint
through `test_api_agents.py`'s new tests against a real Postgres-backed
`TestClient`. Fixed by re-validating through
`AgentDefinition.model_validate({**artifact.definition, **updated_fields})`
instead, which re-runs every nested model's validators.

### Bug found while testing: `AgentArtifactModel.generated_at`'s `onupdate` fires on any row update

Wrote a test asserting a governance PATCH leaves `generated_at` unchanged
(reasoning: it's not a regenerate). It failed -- `generated_at` had
advanced. Root cause: `onupdate=func.now()` on that column (`app/db/
models.py`) is a SQLAlchemy/DB-level "this row was touched" timestamp,
identical to the convention already used by `DraftBpmnModel`,
`BlueprintOverlayModel`, and this same model's own regenerate path --
it fires on *any* UPDATE to the row, not conditionally on which column
changed. The assumption that assigning `artifact.definition` alone
without touching `generated_at` would leave it untouched was simply wrong
given how that column is declared. Fixed by changing the test's
expectation (assert `generated_by` -- a plain column this code path never
touches -- stays the same instead) and correcting `update_agent_governance`'s
docstring, rather than fighting the ORM to suppress `onupdate` for this
one call path.

### Test-authoring bug (not a product bug) while writing gateway coverage: `grade_run`'s empty `expected_steps` means "expect zero steps", not "don't check"

A new `run_scenario` test asserted `status == "passed"` after a permitted
tool call, using a scenario with no `expected_steps` set (assumed empty
meant "skip trace grading"). It failed with `status == "failed"` and a
deviation "Unexpected extra step at index 0" -- `grade_run` (`app/twin/
engine.py`, pre-existing, US14.3) compares the trace pairwise against
`expected_steps` and reports anything not explicitly expected as an extra
step; an empty list means the scenario expects a no-op run, not "don't
grade." The pre-existing `test_run_scenario_end_to_end_pass` already
relies on this (its scenario sets matching `expected_steps` explicitly)
-- this session's new tests just hadn't followed that same convention
yet. Fixed by adding matching `expected_steps` to the new tests, isolating
each assertion to the specific behavior (permission grant/denial) it's
meant to test rather than an incidental grading mismatch.

## 2026-09-29 -- Epic 17, Simulation Run Lifecycle & Execution Trace Model

Implemented all six user stories: a unified `RunStatus`/`StepType`/`RunStep`
taxonomy (`app/schemas/run.py`) shared by twin (Epic 14) and orchestration
(Epic 16) runs, replacing their two independent ad hoc shapes; background
execution for twin runs (US17.6, matching Epic 16's existing pattern);
cooperative cancellation (US17.4); genuine manual human-in-the-loop
suspend/resume (US17.3); and escalation as a distinct outcome (US17.5).
Live-verified: full backend suite (316 tests, up from 307) run against a
real Postgres test DB in a container built from this repo's own
`axyntro-api` image; `tsc -b` + `vite build` + 93-test `vitest` clean on
the frontend; a real manual-checkpoint pause/resume and a cancel exercised
against the live stack (OpenRouter -> Qwen), not just the fake LLM client.

### Migration is additive, not a row-by-row rewrite

New `simulation_runs` table (`SimulationRunModel`) is what both runtimes
write to going forward; `twin_runs`/`orchestration_runs` are left in place
untouched. `list_twin_runs`/`get_twin_summary`/`list_orchestration_runs`/
`get_orchestration_run` now query both tables and merge, mapping legacy
rows through a `_legacy_run_status`/`_legacy_trace_to_steps` projection so
old runs keep displaying correctly under the new shape (old `trace` entries
map onto `TOOL_EXECUTED`/`TOOL_FAILED`/`HUMAN_APPROVED`/`HUMAN_REJECTED`
steps; old `passed`/`failed` status both map to `COMPLETED`, differing only
in `graded_passed`). The epic doc explicitly left "migrate every row" vs.
"additive with a mapping layer" open -- chose the latter: this is
pre-release local-dev/test data, not production history that needs to
stay byte-identical, and preserving every row physically (rather than
requiring a data-migration script) is strictly safer.

### Lifecycle status is decoupled from scenario grading

The epic's own state-machine list (`CREATED -> ... -> {COMPLETED, FAILED,
CANCELLED, TIMED_OUT}`) has no room for "the run finished but grading
disagreed" as a separate case from "the run finished cleanly" -- yet
Epic 20's policy-denial behavior (a denied tool call downgrades the whole
run's status to `"failed"` even when the agent recovers and produces a
correct final output) was exactly that conflation. Fixed by making
`RunStatus` describe execution/lifecycle outcome only; grading (did the
trace/output match `expected_steps`/`expected_outputs`) is now
`graded_passed: bool | None`, computed independently. A run that executes
to a final response is always `COMPLETED`, even with a policy denial or a
grading mismatch recorded via `graded_passed=False` -- "what does status
mean" no longer depends on which kind of run or how it happened to fail.

### Escalation mapping: only a runtime guardrail breach is escalation-eligible, not every failure

`GuardrailExceeded` (`app/twin/gateway.py`) gained a `kind: "timeout" |
"limit"` field. Engine maps `kind="timeout"` to `TIMED_OUT` unconditionally
(a wall-clock cutoff isn't "the agent asking for help"); `kind="limit"`
(tool-call count, tokens, cost, loop detection, or step exhaustion) becomes
`ESCALATED` only when the artifact declares a non-empty
`escalation_policy.escalate_when`, else `FAILED` (Epic 20's original
behavior, preserved when governance isn't opted in). A malformed final
JSON response or an unknown-tool call is always `FAILED`, deliberately not
escalation-eligible -- those are model/programming defects, not the
agent's own guardrail firing.

### Manual pause/resume: engine.py had to become resumable, not just extended

US17.3 ("closes the gap Epic 14's Discovery section explicitly deferred")
needed `run_scenario` to genuinely suspend mid-conversation and resume in a
different process/request later, not just add a third `human_checkpoint_config.mode`
value. Implementation: on a manual checkpoint, the engine serializes
`messages` (already a list of pydantic `ChatMessage`s -- `model_dump(mode="json")`
round-trips losslessly), the `GuardrailTracker`'s counters (new
`to_state`/`from_state` methods, including wall-clock elapsed time so
`max_runtime_seconds` is enforced across the suspend/resume gap, not reset
by it), the steps emitted so far, and any *other* tool calls from the same
model turn not yet resolved (`pending_calls`) -- into one JSON-serializable
`resume_state` dict stored on the run row's own `resume_state` column, read
back and consumed by `execute_twin_run_background`/
`execute_orchestration_run_background` on the next invocation. Orchestration
needed the same treatment one level up (a nested agent's own suspension, or
a manual node's own checkpoint, both propagate as the *orchestration run's*
`WAITING_FOR_HUMAN`) via an analogous `resume_state` shape
(`current_id`/`current_data`/`visited_path`/`node_runs`/`steps`/`manual`/
`node_resume_state`).

### Real defect found only by testing background-task exception safety, not by reading the code

Moving twin runs to background execution (US17.6) exposed a latent
assumption Epic 14 always had: `run_scenario` let `TwinServiceError` (an
unknown tool call, a malformed LLM response while inferring tool schemas)
propagate all the way out, relying on always running inside one HTTP
request where `app/api/twin.py` caught it and returned a 502. A
`BackgroundTasks` callback has no such catcher -- an uncaught exception
there is simply lost, leaving the run row stuck non-terminal forever.
Found by writing `test_run_scenario_invalid_schema_inference_lands_as_failed`
against the real `TestClient` (which runs background tasks for real, not
mocked) and watching it crash instead of returning a graceful `FAILED`
run. Fixed in two places: `run_scenario` itself now catches
`TwinServiceError` (and, as a last resort, any `Exception`) alongside
`GuardrailExceeded`; and `execute_twin_run_background`/
`execute_orchestration_run_background` wrap their own pre-`run_scenario`
setup (tool-schema inference, node-artifact building) in the same
try/except, matching the pattern Epic 16 already used for orchestration's
own setup phase.

### Real defect found via live cancel/resume testing: a `WAITING_FOR_HUMAN` run has no background task to notice `cancel_requested`

First cancel-endpoint implementation just set `cancel_requested=True` and
returned, cooperative-checked by the run's own `on_step` callback -- correct
for a `RUNNING` leg, but a `WAITING_FOR_HUMAN` run has *no* active
background task at all; nothing would ever check that flag until a resume
"woke it up" again, which is backwards (cancelling shouldn't require
resuming first). Found while wiring the frontend's cancel button and
having to reach for a workaround (resume immediately after cancel, just to
trigger the check) -- a sign the backend contract was wrong, not that the
frontend needed to compensate. Fixed `repository.cancel_run`: a
`WAITING_FOR_HUMAN` run resolves straight to `CANCELLED` (no background
task needed, nothing to interrupt), and `resume_run` on an
already-terminal run now raises `TwinServiceError` -> both API endpoints
return `400` for that case (previously unhandled -> would have been a raw
500). Updated `test_cancel_run`/`test_cancel_twin_run` accordingly.

### Frontend blast radius stayed small despite the status-vocabulary rewrite

Confirmed via research before touching anything: only `types.ts`,
`client.ts`, `DigitalTwinPanel.tsx`, `OrchestrationPanel.tsx`, and one test
fixture (`BlueprintPage.test.tsx`) reference `TwinRun`/`OrchestrationRun`/
`NodeRun`/the old `TwinTraceStep`, and usage inside them was shallow
(status-string comparisons for badges/polling, `.trace.length`, a handful
of named fields) -- no other "Digital Twin"-named component touches these
types. Consolidated the two panels' ad hoc status-class functions into one
shared `frontend/src/lib/runStatus.ts` (`runStatusBadgeClass`,
`isTerminalRunStatus`) rather than duplicating the new eleven-value
`RunStatus` switch twice. Added minimal (not redesigned) Approve/Reject/
Cancel buttons to `OrchestrationPanel.tsx` for a `WAITING_FOR_HUMAN` run --
Epic 17 is data-model/execution-plumbing, not new UI (Epic 18 owns
observability), but shipping a backend capability with *zero* way to
reach it from the product would make US17.3/17.4 untestable end-to-end
through the real app.

## 2026-09-29 -- Epic 18, Live Observability: Synchronized Views, Inspector & Replay

Implemented all five user stories: a live step list for both twin and
orchestration runs (US18.1, replacing the flat "N step(s)" count),
`StepInspector.tsx` (US18.2), a Process/Agent/Technical view switch over
one shared step list with synchronized selection (US18.3), BPMN-canvas
highlighting of the live/selected node for orchestration runs (US18.4),
and a replay scrubber with a running cost/token/elapsed readout for
terminal runs (US18.5). Live-verified against the real running stack
(real Postgres, real LLM via OpenRouter -> Qwen) through an actual browser
session (Claude in Chrome), not just component tests.

### Twin runs get the same Process/Agent/Technical treatment, with a static subject line instead of a canvas

The epic's own Notes left open whether a twin run (single agent, no
process graph to walk) needs a separate 2-pane layout. Resolved by reusing
the artifact's existing `primary_node_id` as a static "Agent: X -- node: Y"
header in place of a live canvas -- the Process/Agent/Technical *view
switch* itself and the inspector are identical code paths for both kinds,
only the canvas-vs-static-header choice differs. No schema change needed:
the node reference already existed on `AgentArtifact`.

### Per-step tokens/cost is additive, not a new source of truth

`RunStep` gained `tokens`/`cost_usd`, populated at `MODEL_INVOKED`,
proxy-mode `TOOL_EXECUTED`, and `AGENT_DELEGATED`'s data-adapter call --
but the run-level `total_cost_usd`/`total_tokens` (Epic 17) remain the
only aggregate anything computes from; nothing sums the new per-step
fields to reproduce that total, deliberately avoiding a second
source of truth that could drift out of sync. Old (pre-Epic-18) `RunStep`
rows and every step type with no LLM call of its own (a static-mode tool
response, a human decision) simply have `null` here -- verified in
`test_run_scenario_end_to_end_pass` (static match: `tokens is None`) and
`test_run_scenario_proxy_mode_tool_call_carries_step_usage` (Proxy mode:
`tokens == 2`).

### Three views over one step list, not three simultaneous panes

The requirements describe three *synchronized views*; this app's existing
layout language is compact single-pane-at-a-time (tabs, one `aside` detail
panel), not a triple-pane desktop layout. Implemented Process/Agent/
Technical as three pure filters (`frontend/src/lib/runSteps.ts`) over the
same `RunStep[]`, switched via a tab control, sharing one lifted
`selectedSeq` -- selecting a step in any view drives the same inspector
and the same canvas marker. This is "synchronized" in the sense that
matters (one source of truth, no state to keep in sync across panes)
without a layout rebuild the rest of the app doesn't use anywhere else.

### Real defect found via live testing: a resumed manual checkpoint re-emitted its own request step

(Carried over from Epic 17 live-testing, fixed in this same session before
Epic 18 began: resuming a `WAITING_FOR_HUMAN` run re-emitted a duplicate
`HUMAN_APPROVAL_REQUESTED` for the call it had already asked about before
suspending. Fixed in `app/twin/engine.py`'s `process_calls` -- the first
pending call on a resume no longer re-requests approval it already
requested in the prior leg. Regression-covered in
`test_run_scenario_manual_checkpoint_suspends_and_resumes`.)

### Live testing surfaced two pre-existing, unrelated flakiness points (not Epic 18 defects)

Running a real scenario against HROnboarding (18 steps, 13 generated
agents) twice hit the same Proxy-mode simulation flakiness Epic 17's own
live testing found ("LLM returned invalid JSON/no content while simulating
a response") -- confirmed this lands as a graceful `FAILED` run with a
clear deviation (Epic 17's decision 4), not a crash, exactly as designed;
the live step list and inspector rendered the failure correctly (a
`TOOL_FAILED` step narrated as "Failed calling HRIS API", the inspector
showing the `RUN_COMPLETED` step's `{"status": "FAILED"}` result). Separately,
`execute_orchestration_run_background`'s node-artifact schema-inference
pass (one real LLM call per automatable node, sequential, before the walk
itself starts) took several minutes for this 13-agent process -- an
existing Epic 16 characteristic, not something Epic 18 touches, but worth
flagging: that setup phase isn't a cooperative-cancellation checkpoint
either (`RunCancelled` is only observed once `run_orchestration`'s own
`on_step` starts firing), so a cancel requested during schema inference
has no effect until the walk actually begins. Noted for a future epic,
not fixed here -- out of Epic 18's scope.

## 2026-09-29 -- Epic 19, Evaluation Dimensions, Regression Suites & Version Comparison

Implemented all five user stories: twelve named evaluation dimensions
(US19.1, `app/twin/evaluation.py`), regression suites bound to an agent
definition or process (US19.2, `RegressionSuiteModel`), suite execution
with an aggregate pass count (US19.3, `RegressionSuiteRunModel`), run-vs-run
comparison (US19.4, `app/twin/comparison.py`), and `AgentArtifact.version`
(US19.5, bumped on every regenerate). 324 backend tests (317 existing + 7
new in `tests/test_api_evaluation.py`) pass against the real Postgres test
DB; frontend `tsc -b`, `vite build`, and 104 `vitest` tests all green.

### Evaluation computed in the repository layer, not threaded through RunOutcome

`evaluate_run`'s `latency` dimension needs concrete `started_at`/
`completed_at` timestamps, which only exist as real values once a
`SimulationRunModel` row has been created and (on completion) stamped --
`app/twin/engine.py`'s `run_scenario`/`app/twin/orchestrator.py`'s
`run_orchestration` never touch the DB row directly. Rather than thread
timestamps through `RunOutcome`/`OrchestrationOutcome` just for this,
`repository.execute_twin_run_background`/`execute_orchestration_run_background`
call `evaluate_run` themselves, right after setting `run_model.completed_at`
-- everything else the dimensions need (`steps`, `deviations`,
`graded_passed`, cost/tokens) was already on `outcome`. Orchestration runs
have no single owning artifact, so they use a default `RuntimeGuardrails()`
as the threshold reference for efficiency/latency/token_usage/cost, the
same "no single owner" reasoning behind `RegressionSuiteModel.agent_artifact_id`
being nullable for `kind="orchestration"` suites.

### Two dimensions honestly flagged as structural-proxy-only, not faked

`tool_argument_correctness` and `grounding` have no real structural signal
to check against today: `TwinExpectedStep` records an expected tool
*target*, not expected *arguments*, and nothing fact-checks a final
output's content against the tool results that fed it. Rather than
approximate a check that would silently claim more rigor than it has,
both dimensions always pass with a `detail` string that says exactly what
isn't being verified -- same "flag the gap explicitly" precedent Epic 20
set for its own coverage gaps (sensitive-data masking, tool-argument
side-effect classification).

### Real defect found via testing: naive/aware datetime subtraction in the latency dimension

`SimulationRunModel.started_at`, once loaded from Postgres, comes back as
a naive `datetime` (the column has no timezone), while `completed_at` is
often still the tz-aware value `app.ids.utcnow()` just produced in the
same request, before a commit round-trip through Postgres would strip it.
Subtracting the two raised `TypeError: can't subtract offset-naive and
offset-aware datetimes` -- invisible in isolation (nothing before this
epic ever subtracted these two fields against each other in the same
process), and only surfaced once the real `pytest` suite ran the epic's
own code end-to-end against a real background-task/DB round-trip, not
from reading the code. Fixed in `app/twin/evaluation.py`'s `_latency` by
normalizing both sides to naive before subtracting, rather than changing
either column's declared type (a wider change with no other epic asking
for it).

## 2026-09-29 -- Epic 21, Digital Twin Environments & Fault Injection Catalog

Implemented all four user stories: named/versioned environments (US21.1,
`TwinEnvironmentModel` -- single mutable row, `version` bumped on `PUT`,
same convention as `AgentArtifactModel`), the ten-type fault catalog
(US21.2, `app/twin/environments.py::roll_fault`), scenarios referencing an
environment instead of inline config (US21.3, additive nullable
`environment_id`/`environment_version` on both scenario tables), and
datasets/personas/events as environment-scoped fields (US21.4). 336
backend tests (324 existing + 12 new) pass against the real Postgres test
DB; frontend `tsc -b`, `vite build`, and 104 `vitest` tests all green.

### Scope cut: no separate "named fault set" overlay

The epic's own Notes flagged this as an open question ("needs either a
migration or an additive compatibility path; not decided here"). Resolved
by *not* building a second override mechanism: a scenario's
`environment_id` pulls in that environment's `faults` dict wholesale, and
a scenario's own `system_stubs` remain exactly what they were before this
epic (unaffected by the environment) -- faults intercept a call *before*
Proxy/Static resolution; `system_stubs` control that resolution once no
fault triggers. These are orthogonal concerns, not two things that need
merging, which is what made the "override" question moot rather than
something deferred. If a future need arises for one scenario to reuse an
environment's systems/datasets while overriding just its fault set, that's
a real feature to design then, not implied by anything built here.

### Environments are read fresh at run time, not reconstructed from the pinned version

`environment_version` is recorded on the scenario at creation time (an
audit fact: "this is what version was current when the scenario was
authored"), but `execute_twin_run_background`/
`execute_orchestration_run_background` resolve the environment's *current*
row at run time, same as how an agent artifact's own definition is always
read fresh, never pinned. This is a real, stated limitation, not an
oversight: `TwinEnvironmentModel` keeps no row history (mutate-in-place +
version counter, not a versioned table), so there is no historical
revision N to actually reconstruct once the row has moved to version N+1.
Epic 22's per-run snapshot will record whatever the environment looked
like *at that run*, which is the honest ceiling this data model supports --
not "replay against exactly version N," which nothing here can promise.

### Real defect avoided by a call-count assertion, not found by one

While writing `test_run_scenario_environment_fault_short_circuits_tool_resolution`,
asserting `llm.complete.call_count == 2` (not 3) is what actually proves a
triggered fault skips Proxy mode's own LLM call to fabricate a response --
a looser assertion (just checking the step's `result` dict) would have
passed even if the fault check were wired *after* `resolve_tool_call`
instead of before it, silently paying for an unnecessary simulated LLM
call on every faulted request. Worth naming as a testing decision: this
epic's fault-injection tests assert call counts specifically to catch that
class of "does the fault check actually short-circuit" bug, not just "does
the final result look right."

## 2026-09-29 -- Epic 22, Reproducibility: Execution Snapshots

Implemented all three user stories: a per-run snapshot of every versioned
component that could have influenced the outcome (US22.1), a dedicated
endpoint that returns it verbatim rather than re-resolving anything live
(US22.2), and three fixed, non-data-driven reproducibility-tier labels
(US22.3). 341 backend tests (336 existing + 5 new) pass against the real
Postgres test DB; frontend `tsc -b`, `vite build`, and 104 `vitest` tests
all green.

### Twin vs. orchestration runs snapshot at different points, for a real reason

A twin run's snapshot is built synchronously inside `start_twin_run`,
before the background task is even enqueued -- everything it needs
(the one artifact, the environment) is already resolved there. An
orchestration run can't do the same: which agent artifacts are actually
involved isn't known until `_build_node_artifacts` resolves them inside
`execute_orchestration_run_background`, and that resolution itself makes
LLM calls (schema inference) `start_orchestration_run` has no business
making synchronously inside a request. So the orchestration snapshot is
built one step later, right after `node_artifacts` are resolved and before
`run_orchestration` is called -- still "before execution begins" in the
sense that matters (a cancelled/failed run still has its snapshot), just
not literally at row-creation time like the twin case.

### Snapshot version-pinning has an honest ceiling: environments and artifacts aren't stored as history

The snapshot records the *version number* that was in effect, but neither
`AgentArtifactModel` nor `TwinEnvironmentModel` (Epic 19/21) keep a row per
version -- both mutate in place with a version counter. So "reconstruct
run #8921's exact configuration" means reading the recorded version
*number* and any fields snapshotted alongside it (model policy, tool
contracts, guardrails, escalation policy, knowledge sources, environment
systems/datasets/personas names), not literally replaying against a
byte-for-byte historical row if the artifact/environment has since moved
to a later version. This is stated plainly in code comments
(`app/twin/snapshot.py`) and not glossed over -- building real version
history for two more tables was out of scope for what this epic actually
asked for (a snapshot of what was used, not a full audit-log rebuild of
every prior state).

## 2026-09-30 -- Epic 23, Agent Definition Quality Gate & Unified Release Lifecycle

Implemented all four user stories. 347 backend tests (341 existing + 6
new in `tests/test_api_agent_lifecycle.py`) pass against the real Postgres
test DB; frontend `tsc -b`, `vite build`, and 104 `vitest` tests all green.

### US23.1's required design decision: extend, don't add a third field

Recorded per the epic's own Notes, which explicitly required this be a
stated choice before implementation. Chosen: **`AgentArtifactModel` gets
two new nullable timestamps (`marked_release_candidate_at`, `approved_at`
+ `approved_by`) for the only two lifecycle states that are genuinely
irreducible human actions; every other state in the DRAFT..DEPLOYED
lifecycle is computed on read** (`repository._compute_agent_lifecycle_status`),
the same "derive, don't duplicate and hope it stays in sync" convention
`_is_agent_artifact_stale` already established for US12.4. `AgentArtifact.
lifecycle_status` sits as a third, independent, read-only field alongside
Epic 12's `status` (`generated`/`stale` -- is the *definition* stale) and
Epic 15's `AgentPublishStatus.lifecycle_status` (`generated`/`published`/
`deployed` -- the *publish-event* history) rather than replacing either --
they answer different questions and collapsing them would have lost
information Epic 12/15 already depend on. `DEPLOYED` specifically is
*not* re-derived independently -- it reads the same "any publication ever
marked deployed" fact `get_agent_publish_status` already computes
(`_list_agent_publication_models`), so the two lifecycles can never
disagree about whether something has been deployed.

### `VALIDATED` collapses into `SIMULATION_READY` -- a named scope cut, not an oversight

The requirements' `DRAFT -> VALIDATED -> SIMULATION_READY -> ...` implies
two distinguishable states between "just generated" and "ready to run."
This implementation has no signal that separates them -- both are exactly
"the checklist is satisfied," computed identically. Rather than invent an
arbitrary distinguishing condition with no real meaning (which would be
worse than not having the state at all -- a fabricated distinction nobody
could explain), `_compute_agent_lifecycle_status` only ever returns
`SIMULATION_READY` for that condition; `VALIDATED` stays a valid enum
value (for forward extensibility, e.g. if a real intermediate validation
step is added later) but is never actually emitted today. Documented in
the function's own docstring, not left to be discovered by a confused
future reader wondering why `VALIDATED` never appears.

### The checklist's `test_scenarios` item is the one that actually gates freshly generated artifacts

Every other checklist item is satisfied by generation.py's real defaults
(Epic 20's guardrails/escalation_policy/model_policy precedent, extended
here to `success_criteria`/`completion_conditions`) the moment an artifact
is generated -- confirmed by a test that initially assumed a fresh
artifact would already be `SIMULATION_READY` and had to be corrected once
the real checklist output showed `test_scenarios` as the sole unmet item
(no scenario exists yet at generation time, correctly). Worth naming
because it means the practical gate US23.3 enforces, in this codebase as
built, is almost entirely "has at least one twin scenario been defined,"
not the fuller governance-completeness story the checklist's other fifteen
items suggest -- those other items just don't have much room to be false
given how thoroughly Epic 20 already populates them.

### A real gap found while wiring US23.3: two run-start endpoints had no `TwinServiceError` -> 400 mapping

`app/api/twin.py`'s `start_run` and `app/api/evaluation.py`'s
`start_regression_suite_run` both called into repository functions that,
before this epic, could only raise `NotFoundError` (already mapped to 404
globally) -- so neither endpoint had ever needed its own
`try/except TwinServiceError`. Adding `_require_simulation_ready` (which
raises `TwinServiceError`) surfaced this immediately as an unhandled
500 in a live test run, not a 400 -- fixed by adding the same
`try/except -> HTTPException(400)` pattern `app/api/twin.py`'s own
`resume_run` and `app/api/orchestration.py`'s `start_run`/`resume_run`
already used elsewhere in the same file, rather than inventing a new
error-handling shape.

## 2026-10-09 to 2026-10-10 -- Epics 24-26, Guided Digital Twin Test Builder & Test Data Kitchen

### Hybrid design: wizard backbone + story cards + describe-it, path painter cut

Four alternatives were explored for making the twin tab usable by business
users: (A) a step-by-step wizard, (B) LLM-drafted Given/When/Then "story
cards", (C) painting the test path on the BPMN canvas, (D) a chat copilot.
Built: A as the backbone, B as the content of its "Choose situations" step,
D as a "describe your own situation" box. C was cut to a read-only
plain-language "Expected path" line, because the interactive painter was the
largest build for the least gain over A+B. Everything compiles down to
ordinary `TwinScenario`s (`frontend/src/lib/twinSituations.ts`), so the run
engine, evaluation, regression suites, and the original JSON form (now
"Advanced") are untouched. See `planning/epics/24-guided-twin-test-builder.md`.

### Compile in the frontend, persist the story on the scenario

The business-level situation (behaviours, reviewer, expectations) is stored
verbatim as `TwinScenario.story`, and the deterministic mapping to
faults/stubs/checkpoint config/expected steps is a pure TS function. The
alternative was a server-side compile endpoint, which would have added an
API with no behaviour the client can't do, and made the mapping harder to
unit-test next to the UI that edits it. Server-side, the LLM's output is
still sanitised against known system, field, and coverage-target names
(`app/twin/test_builder.py`), using the same "never trust the echo-back"
rule as `schema_inference.py`.

### New `step_match="in_order"` grading mode -- exact pairwise grading is unusable for business-stated expectations

Epic 14's grader compares actual vs. expected steps pairwise, so any retry
or extra lookup fails the run. A business user can say "the CRM gets
checked, then the manager approves", but can't predict retries. The live
run confirmed this: on a simulated timeout the agent retried three times.
`in_order` requires the expected steps only as an in-order subsequence.
The default stays `exact`, so every existing scenario grades as before. A
missed step does not advance the cursor, so one miss reports one
deviation, not a cascade.

### Per-scenario `faults`, ahead of the environment's

"The ERP is down" belongs to a situation, not to a whole environment.
Epic 21's decision (no separate fault-set overlay) left only "make an
environment per situation", which bloats the environment list. Added
additive `twin_scenarios.faults` (+ `step_match`, `story`), all with server
defaults. The engine offers scenario faults first, then the environment's.
Generated data and personas still go to a per-agent "<Agent> test kit"
environment (Epic 21's existing `datasets`/`personas` fields), so nothing
new was needed for storage.

### Live defect: Proxy-mode system simulator capped at `max_tokens=1000`

Every live guided run failed with "LLM returned no content while
simulating a response". The usage log showed `output_tokens: 1000/1000`:
Qwen's reasoning tokens use up the cap before any text is emitted. This is
the same failure as the `schema_inference.py` 4000->16000 fix committed
just before this work, in a second call site (`app/twin/simulators.py`).
It was invisible to mocked tests by construction, and earlier live passes
evidently had shorter simulated responses. Raised to 8000.

### Live defect: the agent's final answer arrived in a ```json fence

With the simulator fixed, runs still failed: "Final response was not a
valid JSON object". A replay through the engine with a spy client showed
a valid object wrapped in a ```` ```json ```` fence. The final turn runs
with tools attached, so JSON mode is off. `_parse_json_object` in
`app/twin/engine.py` now unwraps a single fenced object and still rejects
prose and non-objects. This affected every twin run on this model, not
only guided ones.

### Live finding: an unconstrained model picked an output field for a threshold reviewer

Persona casting produced "rejects when `Readiness Score/Flag` > 3", an
*output* field the agent would never put in its approval request. The
engine (by design, Epic 14) raises when a rule's field is absent, so that
run could never finish. Threshold reviewers are now limited to the agent's
numeric *input* fields, in the prompt and enforced server-side. An
uncheckable threshold degrades to "rejects", the nearest faithful
behaviour for a strict reviewer.

### Release-gate refusal surfaced up front

Live, 12 of 13 dev agents (generated before Epic 20) are refused by Epic
23's SIMULATION_READY gate. The guided flow would only have shown that
after "Save & run". Step 1's agent cards now list the unmet checklist
items, excluding "test scenarios", which the builder itself satisfies.

### Observed, not fixed: a brand-new user's first request after login can 401

While scripting the live checks, a request right after `POST
/api/auth/login` sometimes returned 401, consistent with the session row
committing after the response is sent. It predates this work and is
outside its scope. Noted here so it isn't lost.

## 2026-10-11 -- Epics 27-35, Organization Context, Ground Truth & Volume Testing

### Epic 27: one generic `context_entities` table, not one table per kind

The epic lists about ten entity kinds (business unit, jurisdiction, role,
approval authority, regulation, obligation, control, policy, glossary term,
plus Epic 29's application and data class). Every kind shares the same
lifecycle: `applies_to` scope, proposed -> confirmed, citations, source,
in-place versioning with history rows. One table keyed by `kind`, with the
kind-specific fields in a JSON `data` column validated by a per-kind
pydantic model on every write (`app/schemas/context.py`'s `DATA_MODELS`),
gives one confirm/version/applicability code path instead of ten. The
cost is that kind-specific fields can't be queried in SQL. Applicability
runs in Python over confirmed entities, which is fine at
single-organization scale. A typed table can be split out later if a kind
needs indexed queries.

### Epic 27: applicability is strict on unit/jurisdiction, lenient on data class, process type, and systems

Where an entity restricts a dimension but the process scope leaves it
empty, two readings are possible. Business unit and jurisdiction are
strict: an unscoped process (every pre-Epic 27 process) sees only
organization-wide entities, so it keeps behaving as before instead of
suddenly receiving every jurisdiction's rules. Data classes, process type,
and systems are lenient: they are usually unknown when a process is first
modelled, and missing "this handles personal data" rules is the worse
failure. Jurisdictions match hierarchically (`EU` covers `DE`, `US` covers
`US-CA`) via the jurisdiction entities' `parent_code`, falling back to a
small built-in table.

### Epic 27: an extracted update to an existing rule is a proposal that merges on confirm

Re-extracting a policy, or extracting a second policy, often restates a
rule that is already confirmed. Creating a second entity would split
history, and editing the live entity in place would skip human
confirmation. Instead the proposal carries `supersedes_id`. On confirm its
content is merged into the existing entity as that entity's next version,
and the proposal row is removed, so everything referencing the entity
keeps referencing it. A proposal that would loosen the rule (drops human
review or guardrails, or raises or removes an approval limit) is flagged
`relaxes`. Such a proposal can't be bulk-confirmed and needs an explicit
`acknowledge_relaxation`. Proposals citing no real passage are dropped,
the same defensive reconciliation as gap analysis.

### Epic 27: embedding model kept at MiniLM for now

The epic asked for an evaluation of a stronger embedding model for
regulatory text. Not done in this pass: a meaningful comparison needs a
labelled retrieval set built from real policies, which doesn't exist yet,
and swapping the model changes the vector dimension of
`document_embeddings` too. Policy passages use the same 384-dim
`all-MiniLM-L6-v2` behind `app/ingestion/embeddings.py`. Retrieval
quality is partly protected by design, because deterministic applicability
narrows the candidate set before any vector ranking (Epic 28). Revisit
once real policy documents are loaded.

### Epic 28: packs are content-addressed rows; staleness is computed on read

A Context Pack is stored once per distinct content (purpose, scope,
process, the exact entity versions and passage ids, budget), keyed by a
hash, and never mutated. Pinning a pack id on a blueprint overlay, agent
artifact, gap finding, or run snapshot therefore pins exact versions.
Staleness is not propagated on write. It is derived on read by comparing
each pinned entry's version with the entity's current version and status,
the same "derive, don't store" choice as Epic 12's artifact staleness. A
context edit can't leave a stale flag out of sync, and no fan-out of
writes is needed. Known limit: a *newly added* rule that would apply to an
existing blueprint doesn't make it stale, because only pinned entries are
compared. The impact view (US28.8) and re-generation cover that case for
now.

### Epic 28: the human-review guarantee is enforced in code, not left to the prompt

US28.5 says a step a policy requires human review for is never fully
automatable. The prompt asks the model for this, and
`apply_blueprint_context` enforces it: any node citing a control with
`requires_human_review` is downgraded from automatable to partial, and gets
a review-before-action checkpoint. The enforcement depends on the model
citing the control for the right step. Live runs cited correctly, but a
model that cites nothing still gets no guard, so the prompt instruction
remains the first line of defence.

### Live finding: models write pack labels into prose meant for people

The first live gap-analysis run against Qwen produced questions like
"...escalates to the MLRO, as required by C3, C6, and C29". It also filed
policy concerns as `structural` findings with no citations, and invented
an `O19` label. Two fixes. The pack header now tells the model to use
codes or names in human-readable text. More importantly,
`mentioned_labels`/`humanize_labels` treat any pack label found in a
question, rationale, or not-automatable reason as a citation, and replace
it with the item's code or name. Tokens that aren't pack labels (e.g. a
"P1" priority) are left alone. On re-run, questions read "...controls
GDPR-C-CONSENT-RECORD, GDPR-LAWFUL-BASIS...", and every policy concern
carried citations.

### Live finding: re-extracting a policy duplicated its rules under new names

Re-uploading the same policy produced "MLRO Review of Potential Sanctions
Match", "...Matches", and "MLRO Review of Sanctions Matches" as three
rules, because the model's titles vary run to run and dedup matched exact
names. Extraction now matches each item against existing confirmed *and*
pending entities of the same kind. It tries the exact code or name first,
then embedding similarity over name plus text (cosine >= 0.85, the local
MiniLM model). A match to a pending proposal is skipped. A match to a
confirmed rule becomes an update proposal. New proposals join the index,
so duplicates within one extraction are caught too. An approval authority
only matches one with the same role.

### Live finding: clause references and title-only passages

Passages were cited as "paragraph 4" while the policy's own heading read
"3. Sanctions screening", and a title-only first line was being retrieved
as a passage. Passages that open with a heading are now cited by it
("3. Sanctions screening (paragraph 4)"), and a lone short title line joins
the passage that follows.

### Epic 29: twin API mode is deny-by-default, checked at registration and again before every call

US29.5 is the riskiest story in the catalog epic: a twin run that reaches
a production system can move real money or real customer data. A
production *deny-list* (refuse anything tagged production) fails open on
the first untagged or mis-tagged endpoint. `app/catalog/endpoints.py` is an
allow-list instead. A call needs all of the following:

- an explicit non-production environment tag;
- an http(s) URL whose host has no production-looking label (`prod`,
  `production`, `live`, `prd`, `prod-*`);
- a host that is recognisably non-production: loopback,
  docker-internal, or `.local`; a `sandbox`/`test`/`dev`/`staging`/`mock`/
  `uat`/`qa` label; or a pattern in `TWIN_API_ALLOWED_HOSTS`.

A production-looking host is refused even when allow-listed. The check
runs when endpoints are registered (a production-tagged endpoint may be
*recorded* for reference, never called) and again inside
`simulators._call_api` before every request, so editing an application's
data through the generic entity endpoint can't route around it. Redirects
are not followed, so an allowed host can't bounce a call somewhere else.
Each scenario still opts into API mode per system (`TwinSystemStub.mode =
"api"`); nothing switches to it implicitly.

### Epic 29: system links live in one per-process table, not in every JSON blob

The epic said "each place a system string is used gains an optional
`application_id`". System strings live in process elements, blueprint
agent specs, agent definitions, and tool contracts, most of them inside
JSON blobs. Adding a field to each would mean four migrations' worth of
reconciliation. Instead `process_system_links` maps (process, system
name) to an application once, and every consumer resolves through it.
Tool contracts get `application_id` as well, because the twin runtime
needs it without a lookup. Auto-links are made only on an exact
(normalized) name or alias match. Fuzzy matches are offered as a pick list
and never auto-linked, and a person's choice, including "not in the
catalog", is never overwritten.

### Epic 29: one tool per system, even with a multi-operation API spec

The twin engine gives the agent one callable tool per system
(`InferredToolSchema`). An OpenAPI spec has many operations. Rather than
change the engine and its gateway, tracing, and grading to many tools per
system, a spec becomes one tool taking `{"operation": <operationId>,
"params": {...}}`. Every operation, with its parameters and response shape,
is listed in the tool description. Proxy mode then fabricates responses
against the real response schemas, and API mode maps the operation back to
its method and path.

### Epic 30: compliance assertions live on the situation's story; an escalated run can now pass

Assertions ("escalated to the MLRO", "nothing done in Core banking", "a
person reviews before acting", "decision logged with an explanation") are
authored on a situation, so they're stored on its `story.expect` (already
persisted JSON). There's no new compiled scenario field, so no migration.
`grade_run` reads them from there. They are structural checks over the
step trace and final output, keeping Epic 14's no-LLM-judge principle.

One behaviour change: an `ESCALATED` run used to be ungraded
(`graded_passed = None`). For a rule that demands escalation, escalating is
the correct outcome. A run that ends ESCALATED and has compliance
assertions is now graded on those assertions. Runs without assertions are
unchanged.

### Epic 30: the release gate reads evidence; it doesn't gate simulation

Epic 23's checklist gates `SIMULATION_READY`, i.e. whether a run may
start. Control coverage can't live there: you need runs to produce it.
It's enforced at `mark-release-candidate` instead. Every applicable,
testable control (the controls recorded in the agent's knowledge sources
by Epic 28) needs a passing situation whose run snapshot matches the
agent's *current* version and pinned context pack, with that pack not
stale. A rule change doesn't delete earlier passes. It turns them
"out of date" in the evidence view and the export, and the gate closes
again until they're re-run. Controls marked `testable: false` (purely
organizational ones, set at confirmation or by extraction) don't count.

### Epic 30: reviewers from the approval matrix are deterministic

US30.3 personas come straight from approval-authority rows with a monetary
limit: a threshold reviewer per row, rejecting above the limit, on the
agent's first amount-like numeric input. No LLM call is involved, so the
endpoint is a GET that viewers can use too. A row with no limit, or an
agent with no numeric input, yields no persona; the LLM casting from Epic
25 is still there for everything else.

### Epic 31: the job queue is Postgres rows with SKIP LOCKED, not Redis/RQ/Arq

The strategy doc suggested Arq or RQ. Both need Redis, a second stateful
service in a stack that otherwise runs only Postgres, and the queue's
volume (connector syncs, ingestion, re-runs) is tiny by queue standards. A
`jobs` table claimed with `SELECT ... FOR UPDATE SKIP LOCKED` gives safe
multi-worker claiming. Jobs survive restarts because they're rows, and a
job whose worker died is requeued when its lease expires. It provides
retries with exponential backoff, per-kind concurrency limits, dedupe keys
(one pending sync per connector), and a scheduler tick. The worker runs in
the API process via the FastAPI lifespan, or separately with `python -m
app.jobs.worker`. When the worker is disabled (tests), the old in-process
`BackgroundTasks` path is used, and tests drain the queue explicitly with
`run_pending`.

### Epic 31: connectors are standards, not vendors

The epic named SharePoint, Confluence, ServiceNow, LeanIX, Entra ID, Okta,
GRC platforms, and data catalogs. Building and live-testing a client per
vendor isn't possible without each vendor's tenant. The built-ins are the
standards those products speak instead:

- MCP: the epic's own "standard interface", a minimal Streamable-HTTP
  client calling one read tool.
- A policy folder: a sync client or export for document libraries.
- A JSON/CSV HTTP feed with a declarative field mapping: CMDB, EA tool,
  GRC, and data-catalog APIs.
- OSCAL catalogs.
- SCIM 2.0, which both Entra ID and Okta serve, for groups.

A specific vendor is a configuration of one of these. The connector
contract is read-only by construction:

- every built-in only issues reads;
- the MCP client refuses tools whose names say they write;
- stored config refuses anything that looks like a secret (`token_env`
  names an environment variable instead).

Synced *entities* are applied as confirmed, because the source is the
system of record. Synced policy *documents* still go through Epic 27
extraction, whose proposals a person confirms.

### Epic 31: model policy is a context variable honoured inside LLMClient

US31.8 needs every LLM call made on a business unit's behalf to obey that
unit's policy: allowed providers, residency, a self-hosted endpoint, PII
redaction. Threading a route argument through all ~10 call sites (and
their callees, such as twin schema inference and simulators) would touch
everything. Instead `llm_route_for(...)` sets a context variable at each
entry point that knows its process (blueprint, gap analysis, chat,
extraction, twin and orchestration runs, the test builder, policy
ingestion), and `LLMClient.complete` enforces it. A blocked call raises
`LLMPolicyError`, which maps to 403.

The provider's own hosting region is configuration (`LLM_PROVIDER_REGION`).
It is empty by default, meaning unknown, and unknown satisfies no
residency requirement: an EU-only unit is refused rather than silently sent
to a provider of unknown location.

### Defect found by testing: business-unit `llm_policy` was silently dropped

The first API-level test of a blocked unit returned 500, not 403.
Entity data is validated per kind, and `BusinessUnitData` had no
`llm_policy` field, so pydantic discarded it on write. No policy applied,
and the call went to the real provider with the test key. `LlmPolicy`
now lives in `app/schemas/context.py` as a typed field on business units,
plus an organization-wide `PUT /api/organization/llm-policy`.

### Epic 31: federation defaults to unrestricted

A user with no business-unit memberships sees and stewards everything,
which is how every user worked before this epic and what a single-unit
organization needs. Memberships restrict visibility to group-wide context
plus the user's units. Owner and `applies_to.business_units` are the
attributes, and another unit's context answers 404, not 403. Stewardship
restricts writes to entities owned by a stewarded unit. Only unrestricted
users manage memberships, organization settings, packs, and the audit
log. Hard multi-tenancy stays out of scope, as the epic says.

### Epic 32: dataset rows are immutable revisions with validity ranges

A run must be able to say exactly which version of a case it was graded
against, and a diff must compare any two versions. Rows are never edited
in place. Each change bumps the dataset version, closes the row's current
revision (`valid_to_version`), and opens a new one. `rows_at(version)` then
reads the dataset exactly as it was, runs pin `(dataset_id,
dataset_version, row_key)` in their own columns and their Epic 22
snapshot, and the diff compares two such reads. The cost is that a large
relabel writes many rows; at the scale of golden sets (hundreds to low
thousands) that's fine.

### Epic 32: a row's decision label is the case outcome, graded against the agent's output

The first draft labelled the expected *human checkpoint* decision. In a
twin, though, the reviewer is simulated, so grading the reviewer's choice
would test the simulation, not the agent. Historical exports also record
the case's *outcome* (approved, declined, referred), not a reviewer step.
A row's `decision` is therefore the expected outcome:
approve/reject/escalate. It is graded against the agent's output field
`decision_field` (default `decision`), after normalizing values such as
"Approved", "declined", or "referred". An ESCALATED run counts as
"escalate", so escalation-expected rows can pass.

### Epic 32: pseudonymization on import is local and deterministic

The epic asked for an option that never sends data to the LLM provider.
Import offers only that option for now. Each mapped column can be kept,
replaced by a code (`anon_<hash>`), or replaced by a look-alike. The
look-alike preserves the shape: emails stay emails, phone and IBAN digit
patterns are kept with the country prefix, and names become invented names.
Replacement is deterministic (a salted hash), so duplicates and joins
survive. Only mapped rows are stored, never the file. LLM-based "clone
and disguise" from Epic 25 remains available for generating look-alikes.

### Live-ish finding while testing: a value-based guess beats a name-based one for the decision column

The column-mapping suggester initially classed "Final Status"
(values: Approved/Declined) as an output label, because only "decision"
in the name triggered the decision role. It now also checks sample
values: a column whose values all normalize to decisions is suggested as
the decision column unless it matches a declared output field. A person
confirms the mapping either way.

### Epic 33: rules are a typed JSON expression language, validated twice, never executed as code

Gateway conditions become `{"all"|"any"|"not"|{"field","op","value"}}`
expressions over the process's known input fields, evaluated by a small
interpreter. Numbers inside strings compare as numbers ("EUR 12,500" > 10000),
and text compares case-insensitively. The LLM drafts one rule per branch.
Every draft is checked against the language and the field list, and
anything that fails is stored as `unstructurable` with the original
condition text and the reason, so it can never execute. A person confirms
each rule, and an edited rule goes back to `proposed`. The oracle takes the
first matching confirmed rule per decision point (or the default branch).
When several decision points determine an outcome, the most severe wins
(reject > escalate > approve), because a case any gate rejects is rejected.

### Epic 33: the oracle never overwrites a label

Oracle labelling fills in only *unlabelled* decisions, with provenance
`oracle`. A row that already carries a historical or expert label is
compared with the rules, never changed. A disagreement is listed as a
conflict for a person, who can raise "the documented rule doesn't match
practice" as a process gap finding (Epic 11). Volume testing then tests
the process documentation as well as the agent. Label-first generation
keeps only the generated records the oracle confirms, and counts the rest
as discarded.

### Epic 33: a judge is trusted only while its calibration is current

Calibration runs the judge on rows that carry a human verdict for the
rubric (`labels.outputs["judgment:<rubric id>"]`) and an agent output from
a run on that row. It records agreement and Cohen's kappa. The rubric
becomes gate-eligible only at or above its `min_agreement` over at least
`min_samples` cases. Calibration stores the rubric version and judge model
it ran with, so any change to the rubric text, field, or model voids
eligibility until recalibrated. This keeps Epics 14/19's
no-LLM-judge-by-default principle: judges are for free-text fields only,
and must earn their place.

### Epic 34: one job per run for correctness, one in-process driver for load

Correctness batches enqueue one `twin_run` job per (row, trial). Each run
is retried on its own, bounded by the worker's per-kind concurrency, and
survives restarts. Before executing, a run checks its batch: paused jobs
re-queue themselves a few seconds later, cancelled or over-budget runs are
marked CANCELLED, and the last run to finish completes the batch. Load
batches can't use that path: the queue's concurrency limit would flatten
exactly the concurrency being measured. A load batch is one `load_batch`
job that drives its runs with `asyncio.gather` under a semaphore of the
requested size. Simulated system latency is applied per run, and the
mode's results are reported separately (Epic 35).

### Defect found by testing: jobs ignored the API's LLM client override

The first batch test failed with 401s from the real provider. Job
handlers called `get_llm_client()` directly, so they bypassed the FastAPI
dependency override the app was configured with. That was hidden in
tests, and in a deployment that swaps the client it would be wrong too.
`get_job_llm` now resolves the client through the app's own dependency
overrides, so a job runs with the same client as the request that queued
it.

### Epic 34: seeds per (batch, row, trial); traces pruned at completion

Each batch run's seed is a hash of (batch, row, trial), stored on the run
and applied as the scenario's checkpoint seed, which also drives fault
rolls. Faults and probabilistic reviewers are therefore reproducible, and
"replay" re-runs one row alone with the same seed. When a batch finishes
with `keep_traces = failed_and_sampled` (the default), it clears the step
traces of passing runs except flaky rows and a 10% sample. The grade,
deviations, final output, and cost stay on every run.

### Epic 34: throttling and caching live in the LLM client

The client bounds concurrent requests per (endpoint, model) with a
semaphore (`LLM_MAX_CONCURRENCY`, default 8). Rate-limit responses still
get the existing exponential-backoff retries. This is distinct from Epic
21's *simulated* 429 faults, which hit the agent's tools. For prompt
caching, the stable prefix (agent instructions, organization context) is
already first in every call. Anthropic models, including via OpenRouter,
also need an explicit `cache_control` marker, which the client now adds.
Providers that cache prefixes automatically need nothing. Whether caching
actually reduced cost on the current Qwen route wasn't measured.

## 2026-10-12 -- Live smoke test of Epics 29-35

Run against the real model (Qwen via OpenRouter) in the isolated
`axyntro-live` container, on the Customer Onboarding process and its
credit-limit agent, using a 10-case labelled dataset (historical import
with pseudonymized IDs and names). Total model spend was about $0.04.

### Labelled runs tell the agent to report its decision

A data-driven run is graded on the case decision, but the agent's own
output schema rarely has a `decision` field, so live every row failed
with "no 'decision' in the output". The twin instructions now add the
scenario's `decision_field` to the required output keys, with its allowed
values, whenever the run has an expected decision. The label itself is
never shown.

### Oracle preconditions are explained up front

Rules are drafted from BPMN gateways (US33.1). A linear process, where
the decision lives in a policy rather than a branch, used to get an empty
list silently; it now gets a 400 explaining why. Label-first generation
checks for confirmed rules before queueing, rather than failing later
inside the job. Drafting oracle rules from confirmed policy obligations
is still open.

### Run duration starts when the run starts

`started_at` was the row's insert time. Batch runs are inserted up front
and queued, so durations included queue wait (a median of 327s against
about 60s of real work), skewing the results page, the load profile, and
the batch estimator. A fresh run now stamps `started_at` when it begins
executing.

### Failure clusters keep the decision direction

Clusters blanked every quoted value, merging "approved what should
escalate" with the reverse. Decision deviations keep their values, which
come from a fixed set.

### The DB session commits before the response is sent

With FastAPI's default request scope, `get_db` committed after the reply
went out. A client calling straight after login could arrive before the
session row existed and get "Session expired". `DbDep` now uses
`scope="function"`, which needs FastAPI 0.121 or later.

### Heading citations survive a merged title

A document title merged into the first clause hid that clause's heading,
so it was cited as "paragraph 1 to paragraph 2". The citation now uses
the first heading in the passage.

## 2026-10-12 -- Demo seed for Epics 27-35

### The demo is seeded through the API, onto a curated diagram

`backend/scripts/demo_seed/seed_demo.py` builds the Customer Onboarding
story through the public API, not by writing rows, so every derived
thing (context packs, citations, links, lifecycle, oracle labels) comes
from the real code paths a presenter will click through. The diagram is
not extracted from a document. It is a fixed BPMN built once with the
app's own builder (`assets/customer-onboarding.bpmn`), because rule
drafting, the oracle, and the agent all key off its three gateways. An
extracted diagram changes shape run to run. The model still drafts the
decision rules (US33.1). The script then sets the six expected
expressions and confirms them, the step a reviewer would do. The demo
runs in its own database (`start.ps1 -Demo`), because the organization
is a singleton.

### Defect found live: one null generated record failed a whole label-first job

The first live seed lost an eight-minute generation job because the
model returned `"record": null` for one of 25 cases.
`test_builder.generate_records` already drops empty records, but
`GeneratedRecord.record` was a required dict, so validation rejected the
whole reply before that filter ran. Mocked tests always returned
well-formed records. A null record is now coerced to `{}` and dropped
like any other empty one.

## 2026-10-13 -- Epic 36, the process digital twin

### Defect found live: the blueprint for a 20-node process came back truncated

Seeding the Invoice to Pay demo (12 tasks, 4 gateways, 4 events, with a
31-rule context pack from policy AP-02) failed at blueprint generation:
`Unterminated string ... (char 58865)`. `evaluate_blueprint` made one call
for the whole diagram, with `max_tokens=16000`. Every node gets a rationale,
an agent spec and rule citations, so the reply grew past the cap and was
cut off mid-JSON. Earlier demos used diagrams of 10-14 nodes with smaller
packs, and mocked tests return whatever JSON they're given, so the limit
never showed. Diagrams larger than 10 nodes are now evaluated in chunks of
8 (`app/blueprint/service.py`, concurrently). Every call still sees the rest
of the diagram as context-only nodes. The trade-off is that consolidation
can no longer span chunks: `consolidated_from_nodes` is trimmed to the
chunk. A reply that stops at the output limit (`finish_reason == "length"`)
now says so instead of surfacing as a JSON parse error.

### Sample-data systems run in-process, not as mock HTTP servers

The twin needed systems that answer the same way for every case, so ground
truth means something. Proxy mode lets the LLM invent a vendor record per
call. The alternative was mock HTTP services behind the existing API mode
(US29.5). That was rejected as the default: customers rarely have sandboxes
for every system, but they do have extracts. An environment's
`system_data` (`app/twin/records.py`) answers lookups from records, with
AND-matching on the given fields and the oracle's case- and
number-insensitive equality. Writes go to a per-run copy, and the agent's
tool is generated from the data, so neither the call nor the schema needs
an LLM. Static and API stubs still win. API mode remains the path to real
sandboxes.

### Gateways are routed by the agent before them, or by confirmed rules

A process twin must route each case on its own facts; Epic 16's
per-scenario `gateway_decisions` can't. There are two data-driven modes,
chosen per gateway. In `agent` mode, the agent before the gateway is given
the branch labels and returns one in `route`, because "matched within
tolerance?" is that agent's judgement to make. In `rules` mode, Epic 33's
confirmed rules are evaluated on the case file, for policy thresholds like
"above EUR 10,000". Asking the agent was preferred over writing rules on
agent output fields, because those field names come from the blueprint LLM
and change between regenerations. Branch labels come from the flow's
condition text. An early version read `FlowRef.label`, which `bpmn/nodes.py`
fills with the *target node's* label; a unit-test fixture had to mirror the
real parser to catch it. An unknown answer falls back to the configured
decision, and otherwise fails the case with the agent's actual words.

### As-is and to-be are costed on the same random draws

The efficiency report (`app/process_twin/efficiency.py`) samples handling
and wait times from the operating model's triangular distributions, seeded
per case. Both sides of a case share the draws, so the difference between
them reflects the process, not sampling noise. Savings intervals are
paired bootstrap intervals over cases. A case the twin couldn't finish is
costed as fully manual on top of what the twin spent. Ignoring it would
have made a failing twin look cheaper.

### Defect found live: an agent on an end event blocked every rehearsal run

With chunked blueprint evaluation, the model no longer saw consolidation
across the whole diagram. It gave three gateways and the end event agent
specs of their own, including a "Process Completion Logger" with no output
schema. `_build_node_artifacts` checked simulation readiness for every node
that had an artifact. The orchestrator never runs an agent on a gateway or
an event, yet that unready artifact failed every run before it started.
Only task-type nodes now get a runnable artifact
(`_TASK_NODE_TYPES` in `db/repository.py`), and the demo seed generates
agents for task steps only. The blueprint still records the model's spec for
those nodes; it simply has no effect on the walk.

### Changing an agent's systems is a governance edit (US20.8)

The process twin's first live run showed agents that couldn't reach the
data their step needs. For example, the supplier check had no sanctions
list in one blueprint, and the matcher had no goods receipts in another.
Regenerating from the blueprint resets every governance edit (Epic 20's
reason for the governance endpoint), so `tools_systems_needed` became a
governance field. Added systems get the same default read contract and
permission that generation gives. Removed systems take theirs with them.
Customised contracts on kept systems are left alone. The alternative,
editing the blueprint node and regenerating, was rejected because it loses
those edits.

### Policy extraction varies a lot between runs

Seeding the demo three times extracted 31, 13 and 9 rules from the same
two-page policy (AP-02). Each run is internally consistent and a person
confirms the proposals, so this isn't a defect in the code path. It is a
quality risk for everything downstream, recorded in
`docs/wiki/Critical-Review.md` (weakness 13). Nothing measures it yet. The
proposed fix is to treat extraction like an agent: a labelled policy set,
plus repeat runs.

### Defect found live: agents' own steps were dropped from orchestration traces (Epic 17)

The first complete Invoice to Pay rehearsal looked as if no agent had
called a tool, yet each agent's node summary showed lookups. The walk passed
the caller's `on_step` straight into each nested `run_scenario`. Nested steps
were therefore persisted incrementally, but never added to the walk's own
`steps` list. That list then overwrote the persisted trace when the run
finished, so Epic 17's "one trace for the whole run" held only while the run
was in flight. Mocked orchestrator tests replaced `run_scenario` entirely, so
nothing exercised the callback path. Nested steps are now relayed through
the walk with its own sequence numbers (`relay` in `twin/orchestrator.py`).
This mattered for Epic 36: the efficiency report counts review requests
from the run trace, and would have undercounted people's time in favour of
the to-be process.

### Defect found live: the data adapter's 1,000-token cap returned nothing

`_adapt_data` (Epic 16) still had `max_tokens=1000`. That is the same cap
that already broke Proxy-mode system simulation and tool-schema inference,
because the model's reasoning tokens count against it. It rarely mattered
while rehearsals were hand-configured. The case file's `auto` mapping calls
the adapter for any input field not already in the case file, and the first
live process twin case failed at its first agent. The cap is now 8,000.

### An agent gets its declared inputs plus the rest of the case file

Blueprint-generated input schemas are loosely named ("Invoice Document",
"Supplier Name/ID", even "Vendor Master Database" as an *input*). The adapter
correctly refuses to invent values and maps them to null. Live, that left
the capture agent with no document reference to look anything up by, and a
clean invoice was rejected two steps later. The alternatives were to make the
adapter guess (rejected: it would fabricate data) or to require exact input
contracts (rejected: generated schemas aren't that precise). With
`handoff="case_file"`, each agent now also receives the rest of the case file
under `case_file`. Its declared inputs stay the contract, and the case file
is context.

### One recorded repair turn when an agent's final answer isn't JSON

In the first full process twin run, 4 of 30 invoices failed because an
agent ended with prose around its JSON. The final turn runs with tools
attached, so JSON mode is off, and only a code fence was tolerated before.
Three options were considered:
- extract any JSON object from the reply: rejected, it hides a real output
  defect and can pick the wrong object
- keep failing: rejected, a production runtime would ask again
- one repair turn: chosen

The repair turn asks once for "ONLY the JSON object with exactly these
keys" (`twin/engine.py`). It's recorded as a `RETRY` step, so the trace
shows it happened. A second failure still fails the run.

### Identical process twin runs disagree

Running the same 30 invoices with the same seed twice gave 67% and 63%
right outcomes, and a number of individual invoices changed outcome. The
seeds fix the simulated reviewers, faults and timings, not the model's
sampling. This is expected of LLM agents, but invisible from one run. It is
why the deck shows the repeat, and why a process-level release decision
should use repeated trials per case (Epic 34's `trials`), not one batch.

## 2026-10-14 -- Epic 37, identity, roles and segregation of duties (build step 1)

### Route guards ask for a permission, scoped by the process's business unit

`EditorDep` was replaced by one `require(permission)` guard per route
(`app/api/deps.py`), with six roles mapped to permissions in
`app/auth/permissions.py`. The alternative, checking role names in routes,
would scatter the role matrix across ~100 routes. The guard reads
`process_id` from the path and walks the scope chain (organization ->
business unit). Per-process ownership, deferred by the user, is one more
link in that chain plus one more `scope_type`; no guard changes. A test
walks the app's routes and fails on any unguarded write.

### Segregation of duties works on change sets, not on roles

Holding the architect and approver roles together is allowed. What's
refused is approving a change you made. Contributions get a global `seq`.
A process version contains the process's contributions after the previous
approved baseline. An agent's pending change set is everything after its
last approval (`approved_through_seq`). Only high-risk contributions
disqualify: any draft write, and any agent generate/regenerate/governance
edit. The user's call was no minor-edit exemption, and strictness only for
these two kinds of change. Test assets and release-candidate marking are
recorded at the standard tier and never block. The high-risk set is data
(`app/auth/sod.py`'s `HIGH_RISK_ACTIONS`). Recording an action at the high
tier also voids the old approval.

Draft writes are recorded inside `repository.set_draft_bpmn`, which now
requires `changed_by` and `action`, rather than at each route. Every route that
changes the diagram (generate, manual edit, chat apply, gap resolve,
version restore) funnels through it, so none can be missed.

### Defect: regenerating or editing an approved agent kept it APPROVED

Found while designing US37.4, by reading `generate_agent_artifact` and
`update_agent_governance`: neither cleared `approved_at`. A definition
approved at v1 stayed APPROVED after a regenerate to v2, and the evidence
export credited the v1 approver. Now any high-risk agent contribution
clears the approval and the release-candidate mark (its test evidence was
for the old definition too).

### Finalize submits; only an approved baseline is used downstream

`get_latest_version` now returns the latest *approved* version, so
blueprint evaluation and decision rules ignore a pending baseline without
any change at their call sites. Existing versions were grandfathered as
approved by the migration (server default), rather than blocking every
process until someone re-approved it. Approval applies to the first
baseline too: it is the largest change of all.

### Publishing now requires an approved agent

US37.4 says a publication needs an approval by someone other than the
publisher. Before Epic 37, publish didn't check approval at all (Epic 15
predates Epic 23's lifecycle). It now returns 409 until the agent is
APPROVED, and 403 when the publisher is its only approver. Still open: the
raw `POST /api/registries/{name}/push` takes an arbitrary definition and
bypasses this. It's guarded by `agent.publish` but not by approval.

### Defect: the startup refusal echoed the settings input

Found by starting uvicorn and the worker with `ENVIRONMENT=production`.
Both refused correctly, but pydantic's ValidationError text includes
`input_value={...}` with every setting, so a database URL with a password
or a key prefix would be printed. `config.settings_or_exit` prints only the
validators' messages and exits 1. Invisible in tests, which assert on
`ValidationError` rather than on what an operator sees.

### Refusals are written in their own transaction

A refused request rolls back, which would take its audit event with it.
`audit.record_refusal` commits in a separate short session. Successful
writes are audited in the request's own transaction, so an event exists
exactly when the action committed.

## 2026-10-15 -- Epic 37, session hardening and revocation (build step 2)

### The CSRF token travels in response bodies, not in a double-submit cookie

US37.12 suggested a double-submit cookie. The frontend runs on :3000 and
the API on :8000. Script on :3000 can read a cookie set by :8000 only
because cookies ignore ports. That breaks as soon as the API moves to
another host. So the token is a random value stored on the session row.
Sign-in, refresh and `/api/auth/me` return it, the client keeps it in
memory, and writes send it as `X-CSRF-Token`. It stays the same for the
life of the session. Rotating it on refresh would break any other open
tab still holding the old token. A stale token (signed in again in
another tab) gets a 403 with `X-Auth-Error: csrf`; the client then
re-reads `/me` and retries once.

### Refresh doesn't require the CSRF header

After a page reload the frontend has no token until a request succeeds.
If the session cookie has also expired, refresh is the only way to get
one. A cross-site page can't send the refresh cookie (SameSite=Lax, path
`/api/auth/refresh`), can't read the response (CORS), and a forced
refresh only rotates tokens the victim already holds.

### Reusing a refresh token within 10 seconds isn't treated as theft

Two tabs hitting a 401 together both present the same refresh token. The
strict rule ("a rotated token came back, so revoke the session") would
sign out the user every time that happened. The client now runs one
refresh at a time per tab, and across tabs there's a 10-second grace
(`sessions.REUSE_GRACE`). Within it, the second refresh gets 200 with the
user and CSRF token but **no new cookies**: the first response already
put them in the shared cookie jar. A thief replaying within the window
gets nothing usable. After the window, reuse revokes the session and is
audited as `refresh_token_reuse` (refused).

### A reuse refusal is returned, not raised

`get_db` rolls back on any exception. Raising the 401 would also roll back
the revocation, and theft detection would do nothing. `/api/auth/refresh`
returns a `JSONResponse(401)` instead, so the revocation and its audit
event commit. `audit.record` gained `outcome=` for this deliberate
committed refusal.

### Secure cookies by default only in production

Python's cookie jar (the demo seed) and httpx's (TestClient) never send a
`Secure` cookie over http, not even to localhost. Development, test and
demo all run on http. `AUTH_COOKIE_SECURE` therefore defaults to secure
only when `ENVIRONMENT=production`, and setting it to false in production
is a startup error.

### Existing sessions are dropped by the migration

Each old session's id *was* its cookie value, stored in plain text.
Turning those into hashed tokens would mean trusting the plain-text
values, so `c8d9e0f1a2b3` deletes them and everyone signs in once more.

### Defect: the route-guard completeness test was checking no routes

Found by regenerating the wiki. The API catalogue still sorted access by
the pre-Epic-37 `EditorDep`, and once rewritten to read each route's
guard, it reported every route as public. FastAPI 0.141 wraps each
included router in `app.routes`, so a walk over `isinstance(route,
APIRoute)` sees only the handful of routes declared on the app itself.
US37.2's `test_every_write_route_declares_a_permission` used that walk,
so it passed while checking none of the ~190 routes in routers. Walking
`fastapi.routing.iter_route_contexts(app.routes)` instead finds all 196,
and every write route was in fact guarded. The test now also fails if it
checks fewer than 100 write routes, so a future FastAPI change can't
quietly empty it again.

## 2026-10-15 -- Epic 37, OIDC sign-in and MFA with Keycloak (build step 3)

### MFA is checked by the API, and Keycloak alone can be talked out of it

Found by the live smoke test (`testing/keycloak/smoke_sso.py`). The
realm asks for a one-time code only when the client requests level 2
(`acr_values=mfa`). A user who deletes `acr_values` from the sign-in URL
in the address bar gets a password-only sign-in, and Keycloak issues a
token with `acr` "pwd". The API refuses it because it checks `amr`/`acr`
itself (US37.8), and audits the refusal. That's the misconfigured-IdP case
the story describes, and it shows up with an ordinary realm, not only a
broken one. The shipped realm keeps this gap open on purpose so the
refusal can be demonstrated. The README says to also set the IdP's
minimum ACR in a real deployment.

### Defect: the API image couldn't start in SSO mode

Found by running the container: `ModuleNotFoundError: No module named
'jwt'`. PyJWT, httpx and cryptography were installed in the local
Python, so all 36 OIDC tests passed, but they weren't in
`backend/pyproject.toml`, so the image didn't have them. They're now
declared. Tests run against a developer's environment can't show a
missing dependency; only a fresh image can.

### Discovery from an internal address, issuer from the public one

Browsers reach Keycloak at `localhost:8180`; the API container reaches it
at `keycloak:8080`. Every token's `iss` must be one fixed value, so
Keycloak's hostname is pinned to the public address. The API fetches
discovery from the internal one (`OIDC_DISCOVERY_URL`), and
`KC_HOSTNAME_BACKCHANNEL_DYNAMIC` makes the token and key endpoints it
finds there use the internal address too. The API still requires the
discovered `issuer` to equal `OIDC_ISSUER` exactly. The alternative,
rewriting endpoint URLs in the API, would be Keycloak-specific code. A
cloud IdP needs neither: its one address works everywhere.

### Group roles from configuration until US37.11

The epic puts IdP group mappings in an admin-managed table (US37.11, step
4). Without some mapping, every SSO user would arrive with no roles and
nobody could grant the first admin. For now, `OIDC_GROUP_ROLES` maps
group to roles (`role` or `role@<business unit name>`), reapplied at
every sign-in. Roles granted this way have `source="group"`. An admin's
role edits leave them alone, and the Users page shows them read-only.
Bad mappings (an unknown role or business unit) don't block sign-in;
they're listed in the sign-in audit event. Entra ID's groups overage
(`_claim_names`) keeps the user's previous group roles rather than
removing them.

### The IdP refresh token decides whether an SSO session lives on

At each of our refreshes, the API redeems the IdP refresh token. If the
IdP refuses it (user disabled, or IdP session ended), our session ends.
That's checked live by disabling a user in Keycloak. If the IdP can't be
reached, the session continues: an IdP outage shouldn't sign everyone
out, and the idle and absolute limits still apply. The token is stored
encrypted (Fernet), with a key derived from the client secret by HKDF,
so there's no extra key to manage. Rotating the secret ends existing SSO
sessions at their next refresh, which is acceptable.

### Refused sign-ins go back to the sign-in page, with a reason code

The callback never shows an error page or raises. It redirects to the
frontend with `?auth_error=<code>`, the sign-in page explains it, and the
refused audit event commits because nothing rolled back. The state is
bound to the browser by a short-lived cookie and is single-use, so a
callback carrying someone else's code (login CSRF) is refused.
Back-channel logout tokens aren't checked for `jti` replay: replaying one
only revokes sessions that are already revoked.

### Logout returns the IdP's logout URL

`POST /api/auth/logout` now returns 200 with `logout_url` (null for local
sign-in) instead of 204. Without RP-initiated logout, the next "Sign in"
would go straight back in on the IdP's session cookie, and signing out
would look broken.

## 2026-10-15 -- Epic 37, SCIM provisioning and group mappings (build step 4)

### Defect: a mapping change would have stripped roles from people not seen since the upgrade

Found by the live smoke test (`testing/keycloak/smoke_scim.py`). The
preview of a change that only *added* a mapping said three people would
lose every role. They'd signed in during step 3, before
`users.idp_groups` existed, so their groups were unknown, and the code
treated unknown as "no groups". Saving would have taken their roles
away. Now only users whose groups are known are recomputed by a mapping
change: everyone provisioned over SCIM, and anyone with a recorded
groups claim. The rest are updated at their next sign-in. This is the
same principle as Entra's groups overage: missing information isn't
evidence of no groups. The test suite couldn't show it: every test user
was created after the column existed.

### Linking a SCIM user to their sign-in by an immutable id

SCIM provisions people before they've ever signed in, so there's no
(issuer, `sub`) to match them on yet. At their first sign-in, the ID
token claim named by `SCIM_LINK_CLAIM` must equal their SCIM
`externalId`. The default is `sub`, which suits Okta and Keycloak when
externalId is the IdP's user id; Entra ID uses `oid`, its objectId.
Email would have been easier and is what the epic forbids: emails are
reassigned. The reverse case is handled too: someone who signed in with
JIT before being provisioned is adopted when SCIM creates them with that
externalId, rather than getting a second account. Live, Olivia was
provisioned with her Keycloak id and linked on her first sign-in.

### SCIM owns a provisioned user's groups

Once a user is provisioned over SCIM, their sign-in groups claim is
ignored. Two sources for one fact can disagree: the claim is cut at the
moment of sign-in, while SCIM pushes changes as they happen. Users who
aren't provisioned keep using the claim, and it's now recorded
(`idp_groups`) so a mapping change applies to them immediately.

### Mappings seed from configuration once, then live in the table

`OIDC_GROUP_ROLES` from step 3 seeds `group_role_mappings` the first time
mappings are needed, so a new deployment's first admin can sign in. It
never seeds again. The audit log records the seeding and every save, so
"has anyone decided the mappings yet" is answered by the append-only log
rather than a flag that could be reset. An admin who deliberately clears
every mapping keeps an empty set.

### What the IdP sends, not just what the RFC says

The protocol layer (`app/scim/protocol.py`) is written for Entra ID's
and Okta's documented requests:
- capitalised ops (`"Replace"`) and string booleans (`"False"`);
- filtered paths (`emails[type eq "work"].value`) and enterprise
  extension URN paths;
- path-less value objects (`{"op": "replace", "value": {"active":
  false}}`);
- group member removal by `members[value eq "..."]`;
- `excludedAttributes=members`.

Okta sends the user's password on create. It's never stored, and a test
checks that. Group PATCH answers 204, which both vendors expect. The
reference sequences are transcribed from the vendors' docs, not captured
from live tenants, and each fixture says so.

### SCIM writes are audited as a service, not a person

The SCIM client isn't a user, so its events have actor type `service`
and are named by the token's label ("SCIM (Entra ID)"). That's the actor
type the epic reserved for service accounts (backlog B4), so no schema
change was needed. Deactivation over SCIM revokes sessions in the same
transaction, and the next request from that browser is refused.

## 2026-10-15 -- Epic 38, the tenant boundary (build step 1)

### Defect: Postgres checks foreign keys without row-level security

The cross-tenant probe (US38.5) found that tenant B could call
`PUT /api/processes/{A's process}/systems/{name}` and get 200. Row-level
security hid A's process from every query, but the route never looked
the process up: it inserted a `process_system_links` row in B pointing at
A's process, and the foreign key accepted it, because Postgres checks
referential integrity as the table owner, bypassing RLS. Six list routes
in the same routers also answered `200 []` instead of 404. None of this
was visible before: with one tenant, a missing process simply had no
children. Five routers (datasets, oracles, batches, process twin, system
links) now declare `ProcessScope` (`api/deps.py`), which 404s a process
the tenant can't see before any other dependency runs, so a permission
check can't answer 403 and confirm it exists. Making foreign keys
tenant-aware in the database (`(tenant_id, process_id)` references)
would close the class of bug for good; it's noted in the epic for
step 3.

### Pooled tables with forced row-level security, failing closed

As decided with the plan: one schema, `tenant_id` on all 60 tenant
tables, a policy `tenant_id = app_current_tenant()` for reads and writes,
`ENABLE` and `FORCE`. `app_current_tenant()` is
`NULLIF(current_setting('app.tenant_id', true), '')`: a transaction with
no tenant gets NULL, so the policy matches nothing and the column
default is NULL, which the policy and `NOT NULL` both refuse. The
`NULLIF` matters: once a connection has used `SET LOCAL`, the setting
reads back as an empty string, not as missing, in later transactions.
Without it, a later transaction with no tenant could have inserted rows
with `tenant_id = ''`. The foreign key to `tenants` would also have
caught that.

### The app connects as a role that can't bypass it

Superusers ignore row-level security entirely, even forced, and the
stack connected as the Postgres superuser `agentic`. So the migration
creates `axyntro_app` (no superuser, no `BYPASSRLS`, owns nothing,
read-only on the platform tables, no `TRUNCATE`), and the API and worker
connect as it. Migrations and `python -m app.tenancy` use
`DATABASE_OWNER_URL`. The API checks its own role at startup:
production refuses to start if isolation isn't enforced, and `/healthz`
reports it everywhere. The test suite connects as the app role too, and
refuses to start as a superuser, or every isolation test would pass for
the wrong reason.

### The tenant is set at the edges and read by the session

Each transaction gets `SET LOCAL app.tenant_id` from a SQLAlchemy
`after_begin` hook, so it ends with the transaction and a pooled
connection never carries a tenant over (also safe under a
transaction-mode pooler). The value comes from `session.info` (the
request's `get_db`) or a ContextVar set at the edges: the tenant
middleware, the worker per job, `tenant_scope()` in scripts and tests.
The epic had said "explicit, never ambient". Passing a tenant argument
to every function that opens its own session would have touched the
audit refusal writer, the local registry and four background tasks for
no safety gain: a missing context fails closed in the database either
way. The middleware is pure ASGI, so the context reaches the thread
pool and background tasks; it's added inside CORS so preflights don't
need a tenant.

### Jobs: the worker walks tenants, one at a time

`jobs` is a tenant table, so a worker sees no jobs until it picks a
tenant. Rather than a cross-tenant claim (a `SECURITY DEFINER` function,
or `jobs` as a platform table), the worker reads the active tenants and
claims each one's jobs as that tenant, starting one tenant further on
each tick. That keeps the queue inside RLS, makes suspension pause a
tenant's jobs for free, and is a first step to fair share (US38.9). Its
cost is a few queries per tenant per tick, fine at tens of tenants and
to be revisited with quotas.

### One identity per (tenant, issuer, subject), without changing lookups

`user_identities`' primary key becomes `(tenant_id, issuer, subject)`,
so one IdP account can exist in two tenants. The ORM keeps `(issuer,
subject)` as its identity (`__mapper_args__`), because within one
transaction RLS shows one tenant, so `db.get(UserIdentityModel, (issuer,
subject))` stays correct and unchanged. User names, connector names,
context-pack hashes and group mappings became unique per tenant the same
way.

## 2026-10-15 -- Epic 38, US38.6: each tenant brings its own IdP

### A tenant's client secret is a prefixed reference, because the admin chooses where it's sent

Moving the IdP settings into the tenant hands its admin two things that
used to be the operator's: the IdP's URL and the name of the variable
holding the client secret. The API sends that secret to the token
endpoint the discovery document names. With a free choice of variable,
a tenant admin could point discovery at a server they control and
reference `env:OPENROUTER_API_KEY`, or another tenant's secret, and
receive it. So references must be `env:TENANT_<SLUG>_...` (the first
tenant may also name the variable the operator configured for it), and
US38.10's secrets store will keep the same shape. For the same reason,
in production the IdP must be public https: the connection test fetches
it from inside the platform, and an internal address would turn it into
a network probe (SSRF).

### The environment is the first tenant's fallback, read at request time

US38.6 says the `OIDC_*` variables "remain only as the defaults for the
first tenant". Copying them into a row at migration time was the obvious
reading, but then `start.ps1 -Sso`, which switches the variables, would
no longer switch sign-in. So nothing is copied: without a saved row, the
first tenant resolves from the environment on each request (cached per
request in `session.info`), with its URLs exactly as configured; other
tenants without a row get no single sign-on. Saving on the Directory page
then takes over. Tests use this too: the test tenant is the first tenant,
so Epic 37's tests, which set environment variables, ran unchanged.

### Redirect URIs come from the tenant's host, for saved settings

A sign-in must come back to the host it started on: the state cookie is
host-only. So a saved tenant's redirect, post-logout and SCIM URLs are
`API_PUBLIC_URL` / `FRONTEND_URL` with the host replaced by the request's
(already an allow-listed tenant domain, so the Host header can't steer
it). The first tenant's environment fallback keeps its configured URLs,
so existing IdP registrations keep working.

### Save only what just passed a connection test

A tenant admin can lock their whole organization out with one typo in
the issuer. The page enables Save only after a passing test of exactly
the values in the form, and the API re-runs the test before saving. The
test checks discovery (whose issuer must match), the endpoints and the
signing keys, and that the secret reference resolves; it can't prove the
secret is right without a real sign-in. `python -m app.tenancy identity
reset <slug>` is the way back if it happens anyway.

## 2026-10-16 -- Epic 38, US38.7: back-channel logout by issuer

### The logout token picks the tenant, its own IdP's keys confirm it

The IdP calls back-channel logout server to server, on whatever host it
was given: Keycloak in Docker calls `api`, the first tenant's host. So
the host can't name the tenant. The endpoint reads the token's issuer
and audience *unverified*, only to choose candidates (the tenants whose
IdP is that issuer with that client), then each candidate verifies the
signature with its own IdP's keys before ending any of its sessions. An
unverified claim never decides what gets revoked; at worst it decides
which keys are tried. The alternative, a platform table of issuers, would
duplicate each tenant's settings outside row-level security; walking the
tenants costs one query per tenant per logout, acceptable until tenant
counts say otherwise.

### Re-importing a Keycloak realm changed everyone's `sub`

Found live: recreating the Keycloak container (to load a changed realm
file) gave every user a new ID, so the next sign-in didn't match the
stored identity and created "Sam Okafor (sam)" beside Sam Okafor; the
SSO and SCIM smoke tests failed on names and links. Users in the realm
files now have fixed IDs (the subjects the local database already held).
The same run showed a second trap: signing the same person in twice
within 30 seconds reuses their one-time code, which Keycloak refuses,
so the smoke test waits for the next code.

## 2026-10-16 -- Epic 38, US38.8: the operator console and support access

### The console connects as a third role that can't read tenant tables

"No standing access" could have been met with the app role (operators
see nothing because no tenant is set) or with the owner. Both can read
tenant tables the moment a tenant *is* set, or bypass row-level security.
The console instead connects as `axyntro_platform`, which has privileges
only on the platform tables and none on any tenant table. So a bug in
the console can't read tenant data at all, row-level security or not,
and the schema test checks that for every table. The platform role
can't delete `tenants` rows either: deletion with evidence is US38.13.

### New platform tables needed an explicit REVOKE

Migration `f2a3b4c5d6e7` set default privileges so every *future* table
grants the app role full access, which suits tenant tables, whose
row-level security does the rest. The operator tables have no tenant and
no policy, so by default the app role could have read every operator's
session hash. Migration `b4c5d6e7f8a9` revokes them, and the schema test
asserts the app role has no privilege on any operator table.

### Usage counts run as each tenant, not as the platform

The console shows counts per tenant (users, processes, waiting jobs, last
sign-in). The platform role can't read tenant tables, and a cross-tenant
aggregate would need a role that can. So the console counts by opening a
short app-role session as each tenant in turn, under that tenant's
row-level security, and returns only the numbers. It's one query batch
per tenant per page view, acceptable until tenant counts say otherwise.

### Support is an ordinary tenant session, entered through a one-time link

A support session had to live on the tenant's host, because cookies are
host-only and the tenant's frontend calls its own host. The console
checks the grant as the tenant and writes a one-time entry (a minute,
stored as a hash); the operator's browser redeems it on the tenant's
host, which starts a normal session for a "support" user. That keeps
every existing rule in force (row-level security, permissions, session
expiry), with three additions checked on every request: the grant must
still be alive, a read-only grant refuses writes, and the request is
audited in the tenant's log in its own transaction, so a refused or
failed request is still recorded. Roles come from the grant and never
include Admin or Approver.

### Keycloak sent one back-channel token for a user with two sessions

Found live: after the operator smoke test left Nora signed in, the next
run's admin sign-out of Nora ended both of her Keycloak sessions but
sent a single logout token, for the older session. The newer Axyntro
session stayed until its next refresh, which asks Keycloak and fails, so
it ends within the 15-minute access window rather than at once. Our
endpoint revokes by `sid` when the token has one, as the spec says. The
smoke test now signs its people out at the end; a `sub`-wide revocation
on admin sign-outs would be the fix if this matters for real IdPs.

## 2026-10-16 -- Epic 38, US38.9: fair-share jobs, per-tenant limits and usage

### Free capacity goes round the tenants a job at a time

The worker used to walk the tenants from a rotating start and let each
claim up to the free capacity, so whoever came first with a big backlog
took every free slot. Now the free slots are handed out one job per
tenant per round, least-served tenant first (fewest jobs running on this
worker), until slots or due work run out. A tenant with one job gets a
slot on the next tick even while another has 10,000 queued; the test
runs it with each tenant first. Each claim is its own short transaction
as the tenant, so row-level security still decides what it can see.

### Limits live on the tenant row, set by operators

Limits could have been a tenant table (an admin edits them) or settings
only (one number for every tenant). They're the hosting company's quota,
so they sit on `tenants`: the platform role already writes it and the
app role can only read it, so no tenant can raise its own. Empty falls
back to a deployment default.

### The budget check reads a monthly total, not the call log

Every LLM call checks the month's spend first. Summing `llm_usage_events`
each time grows with usage; batches at Epic 34 volumes make thousands of
calls a month. `llm_usage_monthly` (tenant, month, operation, model) is
upserted with each call, so the check sums a handful of rows and the
Usage page reads the same table. The per-call rows stay as the input to
billing.

### Requests per minute are counted in-process, concurrent jobs in the database

A cross-process rate needs a shared counter written on every call. The
worker runs in the API process by default, so an in-process window is
the whole deployment there; with a separate worker a tenant gets its rate
in each process, documented. Concurrent jobs are different: they're
already rows, so the limit counts `running` jobs across all workers,
with a per-tenant advisory lock while claiming so two workers can't both
take the last slot.

### Live: the budget stops a real extraction before it reaches the model

`testing/tenancy/smoke_usage.py` ran a real document extraction in
Northwind (recorded as $0.000579, Meridian's usage unchanged), then set
Northwind's budget below that: the next extraction failed with the
budget error, no new call was recorded, and the audit log said why. No
defects; the one wait it needs is the 30-second limit cache.

## 2026-10-16 -- Epic 38, US38.10: per-tenant secrets, files and log lines

### A reference names a secret within the tenant, never a variable

US38.6 let tenants reference their IdP secret as `env:TENANT_<SLUG>_...`
and checked the prefix. Connectors had no such check: `token_env` could
name any variable, including `OPENROUTER_API_KEY`, and the token is sent
to a URL the tenant's admin chose. The LLM policy's `api_key_env` had the
same hole. Rather than add prefix checks in three places, a reference is
now `secret://tenant/<name>`: the tenant comes from where it's used, so
there's nothing to spell that reaches another tenant or the platform.
Environment names stay for the first tenant, whose operator is the
organization on a single-tenant deployment. Checked when saved and again
when used, so a value stored before the rule can't slip through.

### `app.secrets` shadowed the standard library

Found by running the tests: the first name for the package was
`app/secrets`, and `from app import secrets` in modules that also
`import secrets` (the standard library, for tokens) replaced it, so
operator sign-in failed with `token_urlsafe` missing. Renamed to
`app/secret_store`.

### Upload names could leave the uploads directory

Found by testing: Starlette passes the multipart file name through
unchanged, so an upload named `../../../../tmp/x.docx` was written
relative to the process's directory, outside the uploads root. With one
directory per tenant this would also have reached another tenant's
files. File names are now cut to their last component, and every path is
checked to stay inside the tenant's directory; the live smoke test
uploads such a name and finds it inside Northwind's directory.

### Deleting a process left its files

Found live: the smoke tests' processes were deleted but their upload
directories stayed. Rows went with the process (cascade), files didn't.
The delete now removes the process's directory in the tenant's tree.

## 2026-10-16 -- Epic 38, US38.11: model policy, registries and the provider key per tenant

### The monthly budget counts the platform's key only

With a tenant able to call the provider on its own key, the US38.9
budget could have limited all spend or only the platform's. Operators set
it as the hosting company's quota on its own key, so usage rows now carry
`key_source` and the budget, the batch gate and the console's cost count
`platform` only. Usage is still recorded for the tenant either way. If
the tenant's key is referenced but missing, calls are refused: falling
back to the platform's key would put a tenant's spend on the hosting
company's bill without anyone choosing it.

### A clashing Alembic revision id

Found by running the migration: the new migration's id followed the
repo's sequential hex pattern and landed on `e7f8a9b0c1d2`, which Epic
27's organization-context migration already uses; Alembic reported a
cycle. Renamed to `f9a0b1c2d3e4`. Worth checking `grep "^revision"` before
picking the next id in that pattern.

