# Epic 11 -- Process Gap Analysis & Clarification

**Status: implemented.** Drafted from a real, recurring problem and
implemented the same day (see `planning/decision-log.md`'s 2026-09-19
entries for the design decisions, defects found, and live verification).
`app/gap_analysis/` (detection), `GapFindingModel`/`app/api/gap_analysis.py`
(persistence + API), `app/bpmn/validation.py::validate_bpmn_integrity` +
`app/api/versions.py::finalize_process` (Finalize's new gates), and
`frontend/src/pages/GapReviewPage.tsx` (the dedicated pick-an-option UI).

Goal: Treat source documents as inherently imperfect rather than trying to
make extraction or BPMN generation silently paper over that. Detect gaps
and ambiguities in the extracted process -- structural (orphan steps,
missing start/end events, inconsistent branching) and, where multiple
source documents describe the same process, cross-document conflicts --
and resolve them with the user through a specific question and a small
set of options, including an explicit "do nothing," instead of either
guessing (today's extraction behavior) or blocking later with a raw
validator error (today's Finalize behavior, US6.3).

Depends on: Epic 1 (ingestion), Epic 2 (knowledge store / multi-document
merge), Epic 6 (US6.3's structural validation is the immediate,
already-built version of "flag a gap" this epic generalizes and moves
earlier).
Feeds: Epic 3 (fewer broken diagrams to generate), Epic 6 (Finalize should
see fewer/no surprise validation failures by the time a user reaches it),
Epic 7 (blueprint evaluation runs against a more complete, less ambiguous
baseline).

## Why this, not just a better extraction prompt

Three real documents in a row have hit the same class of problem (missing
start/end events; in one case, an internally inconsistent parallel
fan-out where the fan-in existed but two of three fan-out edges didn't --
see the decision log). Tightening `app/ingestion/structuring.py`'s prompt
can reduce this, but real source documents will keep being incomplete or
ambiguous no matter how the prompt is worded, and nothing today handles
two source documents disagreeing about the same step (not yet addressed
anywhere in `app/ingestion/merge.py` either). This epic is about building
a repeatable *process* for surfacing and resolving that class of problem
with the user, not just chasing prompt wording indefinitely.

## User Stories

US11.1 -- Structural gap detection.
As a Process Analyst, I want the system to automatically detect structural
problems in the extracted process (steps with no incoming/outgoing flow,
missing start/end events, disconnected subgraphs) as soon as they exist,
not just when I try to Finalize, so that I can address them while the
document context is still fresh instead of hitting an opaque validator
error later.

US11.2 -- Guided resolution with concrete options.
As a Process Analyst, I want each flagged gap presented as a specific
question with a small set of concrete resolutions (e.g. "'Classify
requirements' has no predecessor -- is this the process start?" with
options like "Yes, add a start event" / "No, it should follow another
step" / "Leave as-is"), so that I don't have to personally diagnose the
underlying BPMN validity problem myself.

US11.3 -- Explicit dismissal, not forced resolution.
As a Process Analyst, I want to dismiss a flagged gap without taking
action, and have it stay dismissed rather than being re-flagged every
time gap analysis runs, so that gaps I've judged to be non-issues don't
keep interrupting me.

US11.4 -- Cross-document conflict detection.
As a Process Analyst, when a process is built from multiple source
documents (Epic 2's merge), I want the system to flag where two documents
describe the same step differently (different actor, different position
in the sequence, materially different label for what looks like the same
step), so that I can decide which version is correct instead of silently
keeping whichever one the merge happened to pick.

US11.5 -- Re-run after changes.
As a Process Analyst, I want gap analysis to re-run after I upload a new
document or make a chat/manual edit, so that the flagged-gaps list never
goes stale against the current state of the process (same principle as
Epic 7's US7.7).

## Design decisions (2026-09-19)

- **Pipeline placement: runs after ingestion.** Gap analysis is a
  proactive step triggered automatically once a document is
  ingested/merged into the process's `ProcessSchema` -- not something the
  user has to remember to run before Finalize. Per US11.5, it also
  re-runs after later document uploads or edits, so it never goes stale.
- **Detection: LLM-based**, for both structural gaps (US11.1) and
  cross-document conflicts (US11.4) -- not split into a deterministic
  structural pass plus a separate LLM pass for conflicts. One call (or
  one call per process, mirroring `app/blueprint/service.py`'s
  one-call-per-diagram pattern) takes the full `ProcessSchema` and
  returns flagged gaps, each with a question and a small set of concrete
  resolution options, using a JSON-schema-contract prompt like
  `app/blueprint/prompts.py`/`app/chat/prompts.py` already do. Note:
  `app/bpmn/validation.py`'s XML/DI-integrity checks (malformed XML,
  dangling shape/edge references, duplicate ids) are a different,
  lower-level concern -- whether the *generated BPMN XML* is well-formed,
  not whether the *process content* has a gap -- and stay as a
  deterministic internal safety net on `app/bpmn/builder.py`'s output;
  they aren't gaps a user resolves through this epic's UI, and shouldn't
  normally fire at all if the builder is correct.
- **Persistence: a dedicated audit table.** Every gap-analysis question
  posed and the user's response (including an explicit dismissal) is
  recorded, not just a per-gap boolean -- a full audit trail of what was
  flagged, when, and what the user decided, per process. New table
  (working name `gap_findings`), not an extension of Epic 2's
  `process_schema_changes` (that log is about schema mutations, not
  questions/answers).
- **Relationship to US6.3: replaces Finalize's existing hard validation.**
  `app/api/versions.py`'s `finalize_process` currently calls
  `validate_bpmn` directly and blocks on its content-completeness issues
  ("no incoming flow", "no outgoing flow"). Those checks move to this
  epic's gap analysis instead -- Finalize should block only on
  *unresolved* gap-analysis findings (found and neither resolved nor
  dismissed), not by re-deriving the same problem from raw BPMN XML.
  `validate_bpmn`'s XML/DI-integrity checks (previous bullet) remain as
  Finalize's structural safety net, since those aren't things gap
  analysis's content-level LLM pass would catch or that a user should
  need to manually resolve.
- **Resolution UI: a lighter, dedicated "pick an option" UI**, not the
  existing chat-ops free-text/diff-confirm flow (Epic 5). Each finding
  renders as its question with selectable option buttons (plus dismiss);
  picking one still applies through the same underlying diff/apply
  machinery Epic 5 already built (`DiagramDiff`/`operations`), just
  without the user having to type a sentence to get there.
