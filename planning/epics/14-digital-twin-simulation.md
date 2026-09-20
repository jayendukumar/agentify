# Epic 14 -- Digital Twin Simulation & Validation

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
