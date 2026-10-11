# Epic 24 -- Guided Digital Twin Test Builder

**Status: all 7 stories implemented** (2026-10-10). Backend:
`app/twin/test_builder.py`, `app/api/twin_builder.py`, `app/schemas/
twin_builder.py`, plus additive `faults`/`step_match`/`story` on
`TwinScenario` (migration `d5e6f7a8b9c0`) and `step_match="in_order"`
grading in `app/twin/engine.py`. Frontend: `TwinTestBuilder.tsx` (wizard),
`TwinSituationCard.tsx`, `TwinSceneEditor.tsx`, `TwinExpectationsEditor.tsx`,
`lib/twinSituations.ts` (situation -> scenario compiler); the original form
moved to `DigitalTwinAdvancedPanel.tsx`. Live-verified against the real LLM.
See the 2026-10-09 decision-log entry, including three defects only the
live run exposed.

Groomed 2026-10-09 from a UX review of the
"Digital Twin Simulation" tab (`DigitalTwinPanel.tsx`), which today asks
the user to hand-write six JSON text areas (`inputs`, `system_stubs`,
`human_checkpoint_config`, `expected_steps`, `expected_outputs`, plus an
environment id) to define one scenario. That works for an Automation
Architect; it shuts out the Process Analyst / Process Owner personas
(see `00-index.md`) who actually know what "a realistic case" and "the
right outcome" look like.

Goal: let a business user build a twin test suite through a guided flow
where the **system proposes** the tests from the agentic blueprint and the
user **chooses, tweaks, and confirms** in business language -- with the
existing JSON form kept intact behind an "Advanced" toggle.

Depends on: Epic 14 (scenarios/runs), Epic 21 (environments, fault
catalog), Epic 23 (a scenario is what moves an artifact to
SIMULATION_READY). Feeds: Epic 25 (the wizard's "Prepare data" step),
Epic 26 (results).

## Research summary (2026-10-09)

- Salesforce Agentforce Testing Center generates test cases from the
  agent's topics/actions, steerable by a natural-language description and
  a positive/negative mix.
- Microsoft Copilot Studio offers a 10-question "quick set" generated from
  the agent's description, a fuller set from knowledge sources, test-chat
  conversion, and CSV import -- always followed by a human review/edit pass.
- Eval practice (dimension-based synthetic generation): define failure-
  oriented axes, let an LLM fill combinations, have a human prune.
- Both vendor tools are still admin-centric; grounding suggestions in the
  *process blueprint* (gateways, checkpoints, systems) and phrasing them
  as stories is the differentiator here.

## Key insight: almost every JSON field is already derivable

| Today's hand-typed field | Already known from |
|---|---|
| `inputs` | `AgentDefinition.input_schema` |
| `system_stubs` keys | `tools_systems_needed` (+ Epic 14's inferred tool schemas) |
| `human_checkpoint_config` | `AgentDefinition.human_checkpoint` |
| `expected_steps` | gateway branches adjacent to the artifact's `node_ids` in the baseline BPMN |
| `expected_outputs` | `output_schema`, `success_criteria`, `completion_conditions` |
| environment faults | Epic 21's ten-type fault catalog |

## Chosen design: hybrid of four explored alternatives

Explored: (A) step-by-step wizard, (B) LLM-drafted "story cards",
(C) paint-the-path on the BPMN canvas, (D) chat copilot. Chosen: **A as
the backbone, B's cards inside step 2, D as a "describe your own
situation" box, C only as a read-only path highlight** -- the full
interactive path painter is the largest build for the least incremental
value over A+B. See the 2026-10-09 decision-log entry.

Wizard steps: **1 Pick agent -> 2 Choose situations -> 3 Prepare data
(Epic 25) -> 4 What should happen -> 5 Save & run**.

## User Stories

US24.1 -- Blueprint-grounded situation suggestions.
As a Process Analyst, I want the system to propose a set of test
situations for an agent -- happy path, each adjacent gateway branch, each
human-checkpoint outcome, and each system failing -- written as short
Given/When/Then stories, so I start from a sensible suite instead of a
blank form. Each card shows what it covers (gateway / checkpoint /
system) so coverage is visible.

US24.2 -- Describe-your-own situation.
As a Process Analyst, I want to type a situation in plain language
("high-value invoice arrives while the ERP is down") and get a structured
situation card back for review, so I can add cases the suggestions
missed without learning the scenario schema.

US24.3 -- Plain-language configuration of systems and reviewers.
As a Process Analyst, I want to set how each system behaves in a
situation (works normally / slow / down / rejects the call / returns bad
data) and how the human reviewer behaves (always approves / always
rejects / rejects above a threshold / ask me live) through simple
controls, never JSON. These compile to `faults`, `system_stubs`, and
`human_checkpoint_config`.

US24.4 -- Expectations pre-filled from the blueprint.
As a Process Analyst, I want each situation's expected outcome (which
systems get used, whether a reviewer is asked and what they decide, which
output fields must be produced) pre-filled and editable as checkboxes,
so grading criteria are visible and reviewable in business terms.

US24.5 -- Coverage meter.
As a Process Owner, I want to see how many of the agent's paths
(gateway branches, checkpoint outcomes, system failures) my chosen
situations cover, so I know what remains untested.

US24.6 -- Scenario carries its story and its own faults.
As an Automation Architect, I want a scenario to persist its
business-readable story and an optional per-scenario fault list (applied
ahead of its environment's faults), so a "CRM is down" situation is
self-contained and the UI can always render the story rather than JSON.
Additive, nullable columns -- every existing scenario keeps working.

US24.7 -- Advanced mode preserved.
As an Automation Architect, I want the existing JSON scenario form and
environment panel still reachable behind an "Advanced" toggle, so no
capability is lost.

## Notes / Open Questions

- Suggestions are an LLM call, so they are never saved automatically --
  the user always confirms (same confirm-before-apply pattern as
  `bpmn-chat-ops`).
- Scenario-level `faults` fire with the scenario's own seeded RNG, same
  as environment faults (`app/twin/environments.py`). A per-scenario
  fault at probability 1.0 is the deterministic way to express "the ERP
  is down in this situation".
- The LLM is told the exact system names, gateway conditions, and output
  fields; anything it echoes back that doesn't match is dropped server-
  side (same "never trust the echo-back" rule as
  `app/twin/schema_inference.py`).
