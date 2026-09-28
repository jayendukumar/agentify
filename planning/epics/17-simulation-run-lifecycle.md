# Epic 17 -- Simulation Run Lifecycle & Execution Trace Model

**Status: not started.** Groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc (Sections 5 and part of 2) against the current
codebase -- see `planning/decision-log.md`'s 2026-09-28 entry for the source
review.

Goal: Replace the two separate, ad hoc run/trace shapes that exist today --
Epic 14's `TwinRun` (`status: passed|failed|error`, a flat list of
`tool_call`/`human_checkpoint` `TwinTraceStep`s, run synchronously to
completion inside one request) and Epic 16's `OrchestrationRun`/`NodeRun`
(its own separate status/kind enums, background-task + polling) -- with one
unified run state machine and typed step taxonomy that both use. This is
foundational: every other epic groomed alongside this one (18-22) builds on
top of this model rather than on either of today's two diverging shapes.

Depends on: Epic 14 (`app/twin/engine.py`, `TwinRun`), Epic 16
(`app/twin/orchestrator.py`, `OrchestrationRun`) -- this epic unifies and
extends their run/trace representations, it does not replace their
scenario definition or grading logic.
Feeds: Epic 18 (observability reads these states/steps), Epic 19
(evaluation reads the typed trace), Epic 20 (its POLICY_CHECK step type and
enforcement hooks land here), Epic 21, Epic 22 (snapshots attach to a run).

## User Stories

US17.1 -- Full run state machine.
As a Platform Engineer, I want every simulation run (twin or orchestration)
to move through `CREATED -> QUEUED -> RUNNING -> {WAITING_FOR_MODEL,
WAITING_FOR_TOOL, WAITING_FOR_HUMAN} -> {COMPLETED, FAILED, CANCELLED,
TIMED_OUT}`, replacing today's flat three-way `passed/failed/error` (Twin)
and `running/passed/failed/error` (Orchestration) status fields, so run
state means the same thing everywhere in the product.

US17.2 -- Typed execution steps.
As an Automation Architect, I want every meaningful action during a run
recorded as a typed step (`RUN_STARTED`, `CONTEXT_RETRIEVED`,
`MODEL_INVOKED`, `DECISION`, `TOOL_REQUESTED`, `POLICY_CHECK`,
`TOOL_EXECUTED`, `TOOL_FAILED`, `HUMAN_APPROVAL_REQUESTED`,
`HUMAN_APPROVED`, `HUMAN_REJECTED`, `AGENT_DELEGATED`, `RETRY`,
`ESCALATION`, `FINAL_RESPONSE`, `RUN_COMPLETED`), replacing today's
two-kind `TwinTraceStep` (`tool_call`/`human_checkpoint` only -- no model
call, context, decision, retry, or delegation steps exist today).

US17.3 -- Manual human-in-the-loop pause/resume.
As an Automation Architect, I want a run to genuinely suspend in
`WAITING_FOR_HUMAN` and wait for a real person to supply the checkpoint
decision later, rather than only the auto-resolved probability/rule modes
Epic 14 built -- this closes the gap Epic 14's Discovery section explicitly
deferred ("today's LLM/backend calls are all synchronous request/response,
so a pause/resume execution state is new territory").

US17.4 -- Run cancellation.
As an Automation Architect, I want to cancel any in-flight run (twin or
orchestration) and have it land in `CANCELLED` rather than either blocking
until it finishes or only ever reaching a terminal state via completion or
timeout, so a stuck or no-longer-needed run doesn't have to be waited out.

US17.5 -- Escalation as a distinct outcome.
As an Automation Architect, I want a run that hits an agent's
`escalate_when` condition (Epic 20) to land in a state distinguishable from
`FAILED`/`TIMED_OUT`, both in storage and in aggregate reporting, so
"the agent correctly asked for help" is never counted the same as
"the agent broke."

US17.6 -- Background execution for twin runs.
As an Automation Architect, I want a twin run (Epic 14) to execute as a
background task polled for status, matching Epic 16's existing pattern,
instead of blocking the HTTP request until the run finishes
(`app/twin/engine.py` today runs start-to-end synchronously inside one
request) -- this is the concrete prerequisite for US17.1/17.3 and for
Epic 18's live observability to have anything to poll for a twin run.

## Notes / Open Questions

This epic is a data-model and execution-plumbing change, not a UI change --
Epic 18 is where the new states/steps become visible to a user. Existing
`TwinRun`/`OrchestrationRun`/`NodeRun` rows should migrate onto the new
shape (or the new shape should be additive with a mapping layer) rather
than the product ending up with three trace formats; the exact migration
approach needs a design pass, not assumed here.

Loop detection (mentioned in the source requirements' guardrails list) is
deliberately *not* scoped here -- it's a limit-enforcement concern that
belongs with Epic 20's runtime guardrails (US20.2), not the state machine
itself. This epic only needs to provide the `RETRY`/`ESCALATION` step types
Epic 20's enforcement will emit.
