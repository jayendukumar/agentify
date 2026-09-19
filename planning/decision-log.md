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
