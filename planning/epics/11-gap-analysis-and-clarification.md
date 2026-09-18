# Epic 11 -- Process Gap Analysis & Clarification

**Status: proposed, not scoped or agreed in detail yet.** Drafted from a
real, recurring problem (see `planning/decision-log.md`'s 2026-09-19
entries) rather than from a spec -- the open questions below need the
user's input before this turns into implementation work.

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

## Open questions -- needs the user's input before scoping further

- **Where in the pipeline does this run?** Automatically right after
  ingestion/merge (proactive, before the user even opens the diagram),
  as a step the user triggers before Finalize (reactive, effectively
  replacing/absorbing US6.3's validation with something more helpful),
  or both?
- **Detection split:** structural gaps (orphan nodes, missing start/end)
  are deterministically detectable straight from `ProcessSchema` with no
  LLM call, the same way `app/bpmn/validation.py` already works. Cross-
  document conflicts (US11.4) look like they need an LLM judgment call
  (or at least embedding similarity from Epic 2's vector store) to notice
  "these two steps are probably the same thing described differently."
  Is v1 scoped to structural-only (cheap, no new LLM call) with
  cross-document conflicts as a v2, or both from the start?
- **Where do dismissed/resolved gaps live?** Needs a persisted record per
  process (US11.3) -- new table, or an extension of an existing one
  (`process_schema_changes` from Epic 2 is the closest existing fit)?
- **Relationship to US6.3:** does this replace Finalize's existing
  validation entirely, or does Finalize keep its hard structural check as
  a final backstop while this epic adds the earlier, friendlier layer on
  top?
- **Resolution UI:** does resolving a gap go through the existing chat-ops
  diff/confirm flow (Epic 5), reusing that machinery, or does it need its
  own lighter-weight "pick an option" UI distinct from free-text chat?
