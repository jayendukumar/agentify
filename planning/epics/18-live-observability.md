# Epic 18 -- Live Observability: Synchronized Views, Inspector & Replay

**Status: not started.** Groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc (Section 6) against the current codebase --
see `planning/decision-log.md`'s 2026-09-28 entry.

Goal: Give a running or completed simulation the three-pane, synchronized,
replayable observability experience the requirements describe, replacing
today's flat trace list (`frontend/src/components/DigitalTwinPanel.tsx`)
and node-by-node timeline (`frontend/src/components/
OrchestrationPanel.tsx`) -- both currently simple lists with no live
in-progress view for twin runs, no cross-view synchronization, and no
replay.

Depends on: Epic 17 (the typed step/state model this reads and polls).
Feeds: Epic 19 (run comparison, US19.4, reuses this epic's per-run detail
view side by side).

## User Stories

US18.1 -- Live execution view.
As an Automation Architect, I want to watch a twin or orchestration run's
steps appear as they happen (polling Epic 17's run/step records), not just
see a final result once the run completes -- today only Orchestration has
any live view at all (`OrchestrationPanel.tsx`'s polling), and even that is
a flat node list, not the described three-pane layout.

US18.2 -- Action / step inspector.
As an Automation Architect, I want to select any step in a run and see its
full detail -- step number, agent, timestamp, duration, action type, tool,
input, output, policy decision, knowledge/evidence used, token usage, cost,
error, retry, result -- without the product depending on hidden model
chain-of-thought to explain what happened.

US18.3 -- Three synchronized views.
As an Automation Architect, I want Process (business-level), Agent
(decisions/escalations/delegations) and Technical (model/API/tool calls)
views of the same run, where selecting a step in one view highlights the
corresponding item in the other two, so I can move between "what happened
in the process" and "what actually got called" without losing my place.

US18.4 -- Process-diagram highlighting.
As an Automation Architect, I want the currently-executing node (live) or
currently-selected step (replay) highlighted directly on the real BPMN
canvas during an orchestration run, extending Epic 16's own note that this
was "left for a follow-up pass" beyond its first-slice timeline list.

US18.5 -- Replay and time-travel.
As an Automation Architect, I want to scrub through a completed run's
timeline and, at any point, see the agent's state, process state, context,
prior actions, tool responses, current decision, and accumulated tokens,
cost, and elapsed time at that point -- not just the run's final summary.

## Notes / Open Questions

This epic is UI-heavy but not UI-only: US18.1 and US18.5 both need Epic
17's run/step records to already carry enough detail (context snapshots,
running cost/token totals per step) for the inspector and scrubber to
render anything meaningful -- confirm those fields exist on the step model
before starting frontend work here.

US18.3/18.4's cross-highlighting needs a stable step-to-node mapping;
Orchestration runs have this for free (`NodeRun.node_id` already ties a
step to a real BPMN node), but a twin run (single-agent, not walking the
process graph) has no equivalent process-level anchor today -- decide
whether twin runs get a simplified two-pane (Agent/Technical) view instead
of the full three-pane one, or whether they should carry a source-node
reference too.
