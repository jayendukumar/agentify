# Epic 19 -- Evaluation Dimensions, Regression Suites & Version Comparison

**Status: not started.** Groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc (Section 7 and FR-OBS-05) against the current
codebase -- see `planning/decision-log.md`'s 2026-09-28 entry.

Goal: Replace today's single binary pass/fail-plus-deviations grading
(`app/twin/engine.py`'s `grade_run`, reused by the orchestrator) with named,
independently-visible evaluation dimensions; let a fixed set of scenarios
be saved as a reusable regression suite bound to an agent definition and
re-run against a new version; and let two runs from different agent
versions be compared side by side.

Depends on: Epic 17 (the typed trace this evaluates), Epic 12 (needs an
explicit, incrementing version identifier per artifact -- today's
`draft`/`generated`/`stale` is a lifecycle state, not a version counter, so
"compare v1.2 vs v1.3" has nothing to key against yet).
Feeds: Epic 23 (its `EVALUATED` lifecycle state is produced by this epic),
Epic 15 (extends US15.3's twin-evidence-at-publish-time with richer
evaluation results).

## User Stories

US19.1 -- Multi-dimensional evaluation result.
As an Automation Architect, I want each run graded on task completion,
policy compliance, tool selection correctness, tool argument correctness,
output correctness, grounding, resilience, efficiency, human intervention,
latency, token usage, and cost -- each shown independently rather than
collapsed into one pass/fail -- so I can tell *which* dimension is weak
instead of only that something failed.

US19.2 -- Named, reusable regression test suites.
As an Automation Architect, I want to save a set of scenarios (twin or
orchestration) as a named suite bound to an agent definition, distinct from
today's ungrouped, individually-run scenario list, so I have one thing to
re-run rather than remembering which scenarios matter for regression.

US19.3 -- Run a suite and get an aggregate result.
As an Automation Architect, I want to execute a full suite against an agent
version and see an aggregate pass count plus each scenario's individual
result (matching the requirements' "10/11 Passed" example), so a whole
regression pass is one action, not N manual runs.

US19.4 -- Side-by-side version comparison.
As an Automation Architect, I want to compare two runs from different agent
versions -- steps, tool calls taken, duration, cost, and outcome -- with
the product calling out specific behavioral differences (different tools
selected, repeated API calls, different escalation decisions, policy
violations, reduced tool usage, different outputs, different
human-intervention needs), so I can judge whether a regenerated agent
actually improved rather than just re-reading two raw traces myself.

US19.5 -- Explicit agent-version numbering.
As a Platform Engineer, I want `AgentArtifact` to carry an incrementing
version identifier that regeneration bumps, in addition to (not replacing)
Epic 12's existing `draft`/`generated`/`stale` status, so runs, suites, and
comparisons have a stable version to key against.

## Notes / Open Questions

**Automated agent improvement (source requirements Section 12)** is
deliberately *not* its own epic here -- the source document itself frames it
as forward-looking ("the architecture *should support future* automated
agent improvement"), not a committed capability. This epic's US19.4
(comparison) and US19.5 (versioning) are exactly the building blocks that
loop would need (`generate v2 -> run same suite -> compare v1 vs v2 ->
human review -> approve`); revisit as a real epic once US19.2-19.5 exist and
there's a concrete "propose v2" mechanism to design against, rather than
speculatively building the loop now.

Grading today (`grade_run` in `app/twin/engine.py`) compares the trace
against `expected_outputs`/`expected_steps`, not an LLM-judge reading
free-text reasoning -- this epic keeps that principle for the new
dimensions above (e.g. "policy compliance" should read Epic 20's
`POLICY_CHECK` steps, not ask a model to re-judge compliance after the
fact) wherever a dimension has a structural signal to check instead.
