# Epic 14 -- Digital Twin Simulation & Validation

**Status: all 6 stories implemented** -- `backend/app/twin/`,
`backend/app/api/twin.py`, `TwinScenarioModel`/`TwinToolSchemaModel`/
`TwinRunModel`/`TwinBaselineModel`; `frontend/src/components/
DigitalTwinPanel.tsx` (the "Digital Twin Simulation" tab on
`BlueprintPage`, US14.1-14.5) and `TwinConfidenceBadge.tsx` (US14.6, wired
into `BlueprintDetailPanel` on the main Blueprint tab). Live-verified
against the real LLM (OpenRouter -> Qwen3.7 Flash) for the core
scenario-execution slice, not just the fake test client -- see
`planning/decision-log.md`'s 2026-09-21 entries, including a real gap in
the shared LLM layer (`ChatMessage` couldn't represent a multi-turn tool
call) found and fixed during that verification. US14.5/US14.6 are pure
CRUD/derived-data additions with no new LLM call sites, covered by the
automated test suite rather than a separate live pass.

**Deliberately deferred, out of this epic's built scope** (see the
"Discovery" section below for the full reasoning): API system mode (a
real dev-provided endpoint) and manual (pause-and-wait-for-a-real-person)
human checkpoints -- the latter needs pause/resume run state that doesn't
exist in this backend yet. Both remain open scope for a future pass if
needed.

Goal: Let an Automation Architect test a generated agent artifact against
representative synthetic scenarios in an isolated environment, so it can be
trusted -- or sent back for revision -- before it's published or deployed
anywhere real.

Depends on: Epic 12 (a generated artifact to execute). Does **not** depend on
Epic 13 -- twin runs are meant to happen pre-publish, against draft or
locally-generated artifacts, not published ones.
Feeds: Epic 15 (publish flow surfaces twin results as a confidence signal).

Numbered ahead of Epic 15 (agent publishing) even though it was the third and
last item in the original backlog (B3) -- see Notes below for why.

## User Stories

US14.1 -- Define test scenarios per process.
As an Automation Architect, I want to define scenario inputs at the
process's start event, plus the expected path through gateways and the
expected end state, so I have representative test cases to run a generated
agent against.

US14.2 -- Isolated execution against synthetic scenarios.
As an Automation Architect, I want to execute a generated agent artifact
against a defined scenario inside an isolated sandbox, so that testing never
touches a real system, API, or document store -- every entry in the
artifact's `tools_systems_needed` is stubbed in twin mode, never called live.

US14.3 -- Per-scenario pass/fail and deviation reporting.
As an Automation Architect, I want each scenario run to report pass/fail
against the expected outcome, and specifically where and why the agent
deviated or fell back to its human checkpoint, so failures are diagnosable
rather than a bare score.

US14.4 -- Aggregate results and cost per run.
As an Automation Architect, I want aggregate results across all scenarios
for an agent (pass rate, common failure points, and LLM cost per run,
reusing Epic 10's usage-tracking infrastructure), so I can judge overall
readiness and expected running cost before trusting it at scale.

US14.5 -- Optional, explicitly-manual baseline comparison.
As an Automation Architect, I want to optionally record a manual baseline
(typical time-to-complete, error rate) for the as-is step being automated,
and compare the agent's simulated results against it, so the "agent vs.
as-is" comparison stays honest about being a user-supplied estimate rather
than data the system silently fabricates.

US14.6 -- Feed results back into blueprint confidence.
As an Automation Architect, I want passing/failing twin results attached
back to the corresponding blueprint node, visible from Epic 8's blueprint
view, so twin evidence informs -- without necessarily gating -- the decision
to generate confidence in and later publish an agent.

## Notes / Open Questions

This is the most speculative item in the original backlog and genuinely
needs a discovery/spike pass before detailed task breakdown -- these stories
describe *what* "digital twin" must mean here (isolated, scenario-based,
mock-tooled, cost-aware), not a committed implementation approach for *how*
to sandbox agent execution safely.

**Real gap found while scoping this:** the current process schema (Epics 1
and 2) captures no timing, error-rate, or frequency data for any step --
there is today no real "as-is baseline" anywhere in the system to
automatically compare against. US14.5 scopes this down to an optional,
explicitly-manual input rather than assuming extraction can produce it. A
future epic could revisit automatic baseline extraction if source documents
turn out to reliably state cycle times/SLAs, but that shouldn't be assumed
here.

**Why this is sequenced ahead of Epic 15 (publishing) despite being listed
last in the original backlog:** testing an agent before it reaches a shared
registry is the safer default order -- a registry entry is visible to other
teams, a draft artifact is not. Epic 15 can still ship before Epic 14 is
fully built if the team wants a "publish now, twin-test later" path, but
that ordering is a deliberate risk trade-off to call out explicitly, not the
recommended default.

## Discovery: human/system/agent simulation model (2026-09-21)

Working assumptions from a design discussion held before any task breakdown
or code. Not yet fully validated -- captured here so the next pass can react
to concrete assumptions instead of a blank page.

**Grounding fact that reframes "sandboxing":** the Epic 12 artifact
(`AgentDefinition`) is not runnable code -- it's a system prompt, an I/O
schema, and a plain list of tool/system *names*
(`tools_systems_needed: list[str]`, e.g. `"CRM system"`). Nothing in the
codebase executes an agent today; `LLMClient.complete()` supports one-shot
tool-calling but there's no multi-turn loop. Since there's no code-execution
tool type anywhere in this system, "isolated sandbox" (US14.2) does not mean
container/VM isolation of untrusted code -- it means an in-process
tool-calling loop where every `tools_systems_needed` entry is wired to a
simulated counterpart below, never to a live integration.

A twin run is really about simulating the *counterparties* an agent talks
to -- humans and systems -- well enough that the agent's own prompt/tool-call
logic can execute for real against synthetic responses. Three participants:

**1. The agent.** The generated `AgentDefinition`, run as an in-process loop
against `LLMClient.complete()`, with every tool call and every
`human_checkpoint` interaction resolved by one of the simulations below
instead of a live system or a live person.

**2. Humans (`human_checkpoint` hops).** Every checkpoint
(`review_before_action`, `review_after_action`, `escalation_on_exception`)
needs a resolvable answer during a run:
- *Auto + probabilistic* -- for a yes/no decision, configure an
  approve/reject probability; sample it automatically each time the
  checkpoint is hit. Keeps runs unattended, but note: not repeatable across
  runs unless the sample is seeded per scenario.
- *Auto + rule-based* -- an optional JSON rule schema expresses a
  conditional over the agent's proposed action/input (e.g. "reject if
  amount > 5000") and deterministically resolves the checkpoint. Takes
  precedence over probability when configured, since it's deterministic and
  scenario-specific. **No structured conditional format exists in the
  codebase to reuse for this** -- checked `ProcessFlow.condition` in
  `backend/app/schemas/common.py`; gateway conditions are a free-text string
  today, not a structured rule. This would be new.
- *Manual* -- don't auto-resolve; pause the run and wait for an actual
  person to supply the decision. This turns a "run" from a fire-and-forget
  batch call into something that blocks mid-execution on user input --
  today's LLM/backend calls are all synchronous request/response, so a
  pause/resume execution state is new territory and is probably the single
  biggest new architectural piece here, more so than "sandboxing" itself.

**3. Systems (`tools_systems_needed` entries).** Each entry needs a
configured simulation mode before a scenario can execute against it -- this
is the resolution to the "unstructured tool name" gap found while reading
Epic 12's output (`tools_systems_needed` has no callable schema, so nothing
can be invoked or stubbed against it as-is). Three modes:
- *Proxy* -- no dev-provided schema. An LLM call infers a plausible
  request/response schema for the named system, then fabricates a plausible
  response per call so execution continues with zero setup. Lowest fidelity
  (schema and responses are guesses), but the convenient default.
- *API* -- a developer supplies both a schema and a real (sandbox/test, not
  production) API endpoint; the twin calls it for real with generated
  parameters. An optional rule file governs how the agent's inputs map onto
  that API's parameters. Highest fidelity, highest setup cost.
- *Static* -- a developer supplies a fixed input -> output table, no LLM and
  no live call involved. Deterministic and cheap; best when a scenario needs
  a guaranteed specific response to force a specific branch.

**How this feeds US14.3 (pass/fail):** decided in the same discussion --
grading compares the tool-call trace (which simulated systems/checkpoints
were hit, with what arguments, in what order) plus the final output against
`expected_outputs`, not an LLM-judge reading free-text reasoning. This
depends on systems having real callable schemas, which is exactly what
Proxy/API/Static all produce -- so a scenario's "expected path through
gateways" becomes an expected sequence of tool/checkpoint calls and their
expected resolutions, not something inferred after the fact.

**Still open, deliberately not decided yet:**
- Where simulation-mode config lives -- per scenario, per artifact (reused
  across that artifact's scenarios), or per `tools_systems_needed` entry
  globally. Leaning toward artifact-level default with per-scenario
  override, but not settled.
- Proxy mode's LLM-inferred schema: generated once and cached on first use,
  or regenerated per call? Caching risks drifting from what the real system
  would actually look like; regenerating risks an inconsistent schema
  within one run.
- The rule-schema format for both human-approval conditions and Static-mode
  input/output tables needs actual design -- there is nothing today to
  extend.
- US14.5's baseline-comparison and US14.6's blueprint-confidence feedback
  haven't been reconsidered against this model yet; they were written
  before this discussion and may need adjusting once execution mechanics
  are settled.
