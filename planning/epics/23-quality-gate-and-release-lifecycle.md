# Epic 23 -- Agent Definition Quality Gate & Unified Release Lifecycle

**Status: not started.** Groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc (Section 8, FR-QG-01) against the current
codebase -- see `planning/decision-log.md`'s 2026-09-28 entry.

Goal: Introduce the `DRAFT -> VALIDATED -> SIMULATION_READY -> SIMULATING
-> EVALUATED -> RELEASE_CANDIDATE -> APPROVED -> DEPLOYED` lifecycle the
requirements specify, and reconcile it with the two status fields that
already exist and would otherwise collide with it on the same artifact:
Epic 12's `AgentArtifact` status (`draft`/`generated`/`stale`) and Epic
15's `AgentPublicationModel` status (`draft`/`generated`/`published`/
`deployed`).

Depends on: Epic 12, Epic 15 (the two existing lifecycles this reconciles
with -- see US23.1), Epic 20 (the validation checklist's items are mostly
Epic 20's new fields), Epic 19 (`EVALUATED` needs an evaluation result to
exist). This is deliberately the last of the epics groomed alongside it
(17-23) -- it should not be started before at least Epic 20 lands, since
most of its checklist has nothing to check without it.
Feeds: nothing further within this grooming pass -- this sits at the top
of the new stack.

## User Stories

US23.1 -- Reconcile with existing lifecycles.
As a Platform Engineer, I want one real design decision (not three parallel
status fields silently drifting) on how the new
`DRAFT...DEPLOYED` lifecycle relates to Epic 12's `draft/generated/stale`
and Epic 15's `draft/generated/published/deployed` -- most likely
extending Epic 15's existing status enum with the new intermediate states
rather than adding a third independent field, but this needs to be an
explicit decision recorded in `planning/decision-log.md`, not assumed by
whoever implements it first.

US23.2 -- Mandatory validation checklist.
As an Automation Architect, I want an artifact blocked from
`SIMULATION_READY` until it has: clear purpose, bounded responsibility,
typed input, typed output, success criteria, completion/stop conditions,
tool contracts, permissions, guardrails, retry policy, escalation rules,
human approval policy, knowledge sources, model policy, runtime limits,
audit configuration, and test scenarios -- almost all of which are Epic
20's new fields, so this checklist is effectively Epic 20's completeness
gate made visible and enforced.

US23.3 -- `SIMULATION_READY` as a real precondition.
As an Automation Architect, I want the UI (and API) to refuse to start a
twin or orchestration run against an artifact that hasn't reached
`SIMULATION_READY`, rather than allowing a run against an artifact missing
required definition pieces and only finding out mid-run.

US23.4 -- Lifecycle evidence flows into publishing.
As an Automation Architect, I want `RELEASE_CANDIDATE`/`APPROVED` state to
carry forward into Epic 15's existing publish flow (extending US15.3's
twin-evidence display) rather than publish re-deriving its own separate
notion of "ready," so there's one readiness signal, not two that can
disagree.

## Notes / Open Questions

US23.1 is flagged as needing an actual decision pass before implementation
starts, the same way Epic 14 itself began with a discovery/spike rather
than a committed design -- record the outcome in the decision log per the
`decision-log` skill's convention, then update this epic's stories to
match the chosen mechanism (extend vs. replace vs. a third field) before
task breakdown.

Twin/evaluation results remain advisory rather than a hard publish gate
today (Epic 15's Notes section, "Twin results are advisory, not a hard
gate"). This epic does not by itself change that -- `RELEASE_CANDIDATE`/
`APPROVED` becoming a genuinely *enforced* precondition for publish (vs.
just visible evidence, per US23.4) would be a deliberate tightening of
that existing decision and should be called out as such if it's chosen,
not slipped in as a side effect of adding the lifecycle.
