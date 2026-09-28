# Epic 22 -- Reproducibility: Execution Snapshots

**Status: not started.** Groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc (Section 10, FR-REP-01) against the current
codebase -- see `planning/decision-log.md`'s 2026-09-28 entry.

Goal: Every run records the exact versions of everything that could have
influenced its outcome, and that configuration can be reconstructed later
-- while being explicit that this guarantees *configuration*
reproducibility, not that re-running produces the identical output (LLM
calls are not assumed deterministic).

Depends on: Epic 17 (the run record this attaches to), Epic 20 (policy,
knowledge-source, and tool-contract versions to snapshot), Epic 19
(agent-version numbering, US19.5), Epic 21 (twin-environment and dataset
versions to snapshot) -- this epic snapshots fields the others introduce,
so it should be sequenced last among 17-22 in any implementation pass.
Feeds: Epic 23 (a release candidate's evidence trail relies on being able
to reconstruct the runs that justified it).

## User Stories

US22.1 -- Per-run execution snapshot.
As an Automation Architect, I want every run to record the agent version,
prompt version, model config version, tool-contract versions, policy
version, knowledge-source versions, twin-environment version, dataset
version, and scenario version/id that were in effect -- matching the
requirements' "Run #8921" example -- so a run's exact configuration is
never ambiguous after the fact.

US22.2 -- Reconstruct a historical run's configuration.
As an Automation Architect, I want to give a run id and see every
versioned component it used resolved and displayed, even after later edits
to any of those components, so I can understand exactly what a past run
was testing without cross-referencing multiple change histories by hand.

US22.3 -- Explicit reproducibility-tier labeling.
As an Automation Architect, I want the UI to distinguish configuration
reproducibility (guaranteed -- the snapshot above), behavioral
reproducibility (best-effort -- same config, plausibly similar behavior),
and exact-output reproducibility (not guaranteed for LLM calls), so nobody
reads "we can reconstruct this run's config" as "re-running it will
produce the same transcript."

## Notes / Open Questions

This epic is close to pure plumbing once Epics 17/19/20/21 exist -- the
main design question is *where* the snapshot lives: denormalized onto the
run record itself (simplest, matches Epic 16's "immutable evidence once
complete" convention) vs. a separate snapshot table keyed by run id
(avoids widening the run record, but adds a join everywhere a snapshot is
displayed). Lean toward denormalized-on-the-run, consistent with how Epic
14/16 already keep a run's full trace as one JSON blob rather than
normalizing it out, but not decided here.
