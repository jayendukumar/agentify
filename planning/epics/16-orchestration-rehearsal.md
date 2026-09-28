# Epic 16 -- Orchestration Rehearsal (multi-agent process rehearsal)

**Status: all 7 stories implemented** -- `backend/app/twin/orchestrator.py`
(graph walk, gateway/manual-node resolution, LLM data adapter),
`backend/app/db/repository.py`'s orchestration section (CRUD +
`execute_orchestration_run_background`), `backend/app/api/orchestration.py`,
`OrchestrationScenarioModel`/`OrchestrationRunModel`, and
`frontend/src/components/OrchestrationPanel.tsx` (the "Orchestration
Rehearsal" tab on `BlueprintPage`). Live-verified against the real LLM
(OpenRouter -> Qwen3.7 Flash) for the one genuinely new LLM call site (the
data-adapter reshaping call), and against a real, previously-finalized
process (`HROnboarding`) through the actual UI -- the latter surfaced and
fixed a real gap (not every finalized diagram has explicit start/end
events; see "Execution model" below) and confirmed the parallel-gateway
guard fires correctly against real production data. See
`planning/decision-log.md`'s 2026-09-28 entries. Parallel gateways remain
explicitly unsupported (see "Still open" below).

Goal: Let an Automation Architect run an entire blueprint's worth of
generated agents together, end to end along the real process flow, so they
can see how the *orchestrated* solution behaves as a whole -- not just one
agent in isolation -- before trusting or publishing any of it.

Depends on: Epic 7 (blueprint overlay, one verdict/agent_spec per node),
Epic 12 (a generated `AgentArtifact` per automatable node/group), Epic 14
(the digital twin engine -- this epic runs *on top of* it, one node at a
time, rather than replacing it).
Feeds: Epic 15 (publish flow) the same way Epic 14 does -- an orchestration
run is pre-publish evidence, not a gate.

## Why this is a new epic, not an extension of Epic 14

Epic 14's digital twin engine (`app/twin/engine.py`) already does real,
tool-stubbed, cost-tracked execution of **one** generated agent against
**one** scenario. What it does not do -- and what a "test the
orchestration" request actually needs -- is walk the process graph and run
every automatable node's agent *together*, handing data from one agent's
output to the next agent's input, resolving gateways, and simulating the
non-automatable (manual) steps in between. That's the gap this epic closes,
reusing Epic 14's engine as the per-node execution primitive rather than
duplicating it.

## User Stories

US16.1 -- Define a process-level rehearsal scenario.
As an Automation Architect, I want to define one scenario per process --
start-event inputs, how to resolve each gateway the scenario should hit,
how each non-automatable node should be simulated, and any per-node system-
stub overrides -- so I have a repeatable, end-to-end test case for the
whole orchestrated solution, not just one agent.

US16.2 -- Walk the real process graph, not a flattened list.
As an Automation Architect, I want a rehearsal run to follow the finalized
diagram's actual sequence flows (including gateway branches) rather than a
simple top-to-bottom node list, so the run reflects what the process
actually does, including branching and rework loops.

US16.3 -- Run every automatable node's agent for real, in sequence.
As an Automation Architect, I want each automatable node's generated agent
to execute for real (same tool-stubbed, cost-tracked execution as Epic 14)
as the walk reaches it, receiving the previous step's output as its input,
so the run demonstrates actual agent behavior, not a mocked stand-in.

US16.4 -- Simulate non-automatable (manual) nodes along the way.
As an Automation Architect, I want a non-automatable node to be simulated
too, not skipped -- by default as a human-checkpoint approve/reject
decision (reusing Epic 14's simulation), with the option to configure it as
a fixed output stub instead -- so a manual step doesn't silently vanish
from the rehearsal.

US16.5 -- Adapt data across agent handoffs automatically.
As an Automation Architect, I want the system to reshape one agent's
output into the next agent's expected input by default (an LLM adapter
call), with the option to require an exact field-name contract between two
specific agents instead, so mismatched I/O schemas don't block a rehearsal
run but a strict contract can still be enforced where it matters.

US16.6 -- See pass/fail and deviations for the whole run.
As an Automation Architect, I want the finished run graded against the
scenario's expected path and expected final output, with deviations
attributed to the specific node/handoff where they occurred, so a failure
is diagnosable across the whole chain, not just within one agent.

US16.7 -- Observe a run live while it executes.
As an Automation Architect, I want to watch the rehearsal progress node by
node while it's running (which agent is active, what it called, what
handed off to the next node) rather than waiting for one opaque result at
the end, so a slow or stuck multi-agent run is visible and diagnosable in
real time.

## Locked design decisions (2026-09-28)

**Non-automatable (manual) node simulation:** default to Epic 14's
human-checkpoint simulation (approve/reject, probability or rule mode),
with an explicit opt-in per node to a fixed-output stub instead. Rationale:
most manual steps in a real process *are* a decision gate (approve, sign
off, review), so reusing the existing checkpoint simulation is the more
realistic default; a fixed stub is the deliberate escape hatch for a
manual step that's really just a data-producing action (e.g. "file the
document") with no real decision in it.

**Cross-agent data handoff:** default to LLM-adapter reshaping (a small,
logged LLM call that reshapes the upstream node's output into the exact
shape the downstream agent's `input_schema` declares), with an explicit
opt-in per handoff to an exact field-name contract (no LLM call; missing
fields are a hard error) where a specific pair of agents is meant to be
strictly wired together. Rationale: agent I/O schemas are independently
generated per node and won't reliably share field names; requiring an
exact contract everywhere would make most rehearsals fail on naming alone,
but a strict mode needs to exist for handoffs an architect has actually
locked down.

**Persistence:** reuse Epic 14's `TwinRun`-style conventions -- one
`OrchestrationRun` row holding the full node-by-node trace (JSON), kept
forever once complete (never mutated after `status` leaves `"running"`),
rather than a separate table per node-run. The run row *is* mutated while
`status == "running"` (node_runs appended incrementally) specifically to
support US16.7's live observability -- this is the one deliberate
exception to the "runs are immutable evidence" convention Epic 14 set,
justified by the same UX gap that made background-task polling necessary
for document ingestion (Epic 1).

**Live observability mechanism:** background task + polling, not
websockets/SSE. The codebase already has exactly this pattern for a
long-running operation (`app/ingestion/pipeline.py`, a `BackgroundTasks`
job with its own DB session, polled every 4s by the frontend per
`ProcessDetailPage.tsx`) -- reusing it keeps this epic's moving parts to
"one more pollable background job," not a new transport mechanism, per the
project's standing "smallest number of moving parts" preference
(`local-stack-bootstrap` skill).

## Execution model

1. **Graph source**: the same `FlowNodeInfo` graph Epic 7 already builds
   (`app/bpmn/nodes.py:extract_flow_nodes`, parsed from the blueprint's
   `baseline_version_id` finalized XML) -- not the live/mutable process
   schema tables, so a rehearsal always runs against exactly what the
   blueprint was evaluated against.
2. **Walk**: start at the `startEvent` if the diagram has exactly one,
   follow `successors` one at a time. Finalize only checks XML
   well-formedness, not narrative completeness (`app/bpmn/validation.py`'s
   `validate_bpmn_integrity` vs. the separate, non-blocking
   `validate_bpmn`), so a real finalized diagram can legitimately have no
   explicit start/end events at all -- found by rehearsing a real process
   (see the decision log). When there's no `startEvent`, the entry point
   falls back to whichever node has no incoming flow at all, erroring
   clearly if that's ambiguous (zero or multiple candidates) rather than
   guessing. Symmetrically, a node with no outgoing flow always ends the
   walk cleanly (its data becomes `final_output`), not only when it's
   literally tagged `endEvent`.
   - A node whose BPMN type contains `"Gateway"` is pure control flow: no
     agent runs there. The scenario's `gateway_decisions[node_id]` says
     which successor to follow; an unconfigured gateway the walk actually
     reaches is a hard error (not a silent default), since guessing a
     branch would misrepresent the rehearsal.
   - A node with exactly one successor just proceeds to it automatically
     (no scenario config needed) -- only true decision points require
     configuration.
   - A task-type node with an `AgentArtifact` (verdict automatable/partial)
     runs that artifact for real via Epic 14's `run_scenario`, after
     adapting the current data into that artifact's `input_schema` shape
     (US16.5). Its own `human_checkpoint` (if any) is resolved the same
     way Epic 14 already does, using a per-node override if the scenario
     supplies one, defaulting to always-approve otherwise.
   - A task-type node with no artifact (not automatable) is resolved per
     US16.4's manual-node config.
   - A hard step-count ceiling (config, default 50) guards against a
     rework loop turning into an unbounded/runaway-cost run -- exceeding it
     ends the run with `status="error"`, not a silent truncation.
3. **Grading**: after the walk ends, compare `visited_path` against the
   scenario's `expected_path` (if given) and `final_output` against
   `expected_final_output`, the same "compare the real trace, not an
   LLM's opinion of it" principle Epic 14's `grade_run` already uses.
4. **Cost/tokens**: summed across every node's agent run and every LLM
   adapter call, using Epic 10's existing `estimate_cost_usd` -- same
   "unknown beats a wrong partial number" rule as Epic 14's aggregates if
   any model along the way is unpriced.

## Still open, deliberately not decided yet

- Parallel gateways (`parallelGateway`): the execution model above assumes
  a single active thread of control (one `current_id` at a time). A true
  parallel split/join needs its own design pass (running branches
  concurrently, joining their outputs) -- out of scope for this epic's
  first slice; a `parallelGateway` reached by a scenario should error
  clearly rather than silently behaving like an exclusive gateway.
- Whether a failed/erroring node should ever let the walk continue past it
  in some "best effort" mode, versus always stopping the whole run --
  current design always stops on a node `status="error"` (unrecoverable:
  bad JSON, exceeded turns, unresolved gateway) and always continues past
  a node that completed cleanly, even if that node's own local grading
  would have flagged something -- since per-node scenarios built for this
  epic don't carry their own `expected_outputs`, this distinction mostly
  doesn't bite yet, but will need revisiting if that changes.
- UI depth for US16.7 beyond a live per-node timeline (e.g. a synchronized
  highlight on the BPMN canvas itself) is left for a follow-up pass; the
  first slice is a timeline list, reusing `DigitalTwinPanel.tsx`'s
  polling/display conventions rather than the canvas.

## Amendment (2026-09-28): scoped into follow-on epics

Reviewed against the "Digital Twin Agent Simulation" requirements doc
alongside Epic 14 (see that epic's own 2026-09-28 amendment for the full
list). Two points specific to this epic:

- **Epic 17** unifies this epic's `OrchestrationRunStatus`/`NodeRunStatus`/
  `NodeRunKind` enums with Epic 14's `TwinRunStatus` into one shared run
  state machine and typed step taxonomy -- this epic's own separate enums
  become the thing Epic 17 supersedes, not a second parallel model to
  maintain.
- **Epic 18** is the "follow-up pass" this epic's own Notes section above
  already flagged for canvas-synchronized highlighting (US16.7) -- that
  work is now scoped as Epic 18's US18.4, not left indefinitely open here.

This epic's own status and shipped scope are unchanged.
