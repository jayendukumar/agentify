# Epic 26 -- Business-Readable Twin Results & Tab Consolidation

**Status: US26.1 and US26.3 implemented** (`TwinSuiteResults.tsx`:
story-first headline, plain-sentence deviations, details collapsed, "Run all
situations"). US26.2 ("Fix this" links) and US26.4 (one testing tab) are
groomed, not yet built. Groomed 2026-10-09 alongside Epics
24 and 25.

Goal: results should read like the situations did -- in business
language, leading with "did it behave as expected" -- and the four
twin-related tabs on the Blueprint page (Digital Twin Preview, Preview
Orchestrated, Simulation, Orchestration Rehearsal) should become one
coherent "Test your agents" experience.

Depends on: Epics 17-19 (run status, evaluation dimensions), Epic 24.

## User Stories

US26.1 -- Story-first results.
As a Process Owner, I want the suite's results headlined as "N of M
situations behaved as expected", each situation showing its story and,
on failure, one plain sentence about what differed (built from the
run's deviations), with cost, tokens, the baseline comparison, and the
full trace collapsed under "Details".

US26.2 -- "Fix this" links.
As a Process Analyst, I want a failed situation to link to the part of
the agent definition most likely responsible (missing tool, wrong
checkpoint, output field not produced), so failures lead to action.

US26.3 -- Run the whole suite.
As a Process Analyst, I want one "Run all situations" action with a live
progress strip, instead of pressing Run per scenario.

US26.4 -- One testing tab.
As any persona, I want a single "Test your agents" tab with *Single
agent* and *End-to-end process* modes, replacing the four separate twin
tabs, so the testing story is in one place.

## Notes

- US26.4 touches OrchestrationPanel and both Preview tabs. It is
  deliberately sequenced after the single-agent wizard has been used
  for real, so the merged layout is informed by usage rather than
  guessed.
