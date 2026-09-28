# Epic 21 -- Digital Twin Environments & Fault Injection Catalog

**Status: not started.** Groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc (Section 4, FR-DT-01 through FR-DT-04)
against the current codebase -- see `planning/decision-log.md`'s
2026-09-28 entry.

Goal: Promote "digital twin environment" and "fault" from configuration
embedded inline in a single scenario into named, versioned, reusable
entities, and broaden fault injection from what today's Static/Proxy
system-stub modes can already sort of express into the explicit fault
catalog the requirements list.

Depends on: Epic 14 (`TwinScenario.system_stubs`), Epic 16
(`OrchestrationScenario.system_stubs`) -- both currently embed
per-system stub config directly inline in a scenario; this epic factors a
reusable environment out from under them rather than replacing the
Proxy/API/Static simulation mechanism itself.
Feeds: Epic 19 (regression suites, US19.2, reference an environment + fault
set, not just an ungrouped scenario).

## User Stories

US21.1 -- Named, versioned Digital Twin Environment.
As an Automation Architect, I want to define a reusable, versioned
environment (e.g. "Onboarding Twin v1") bundling the systems, synthetic
datasets, personas, and events relevant to a process, so multiple agents'
scenarios for that process share one environment definition instead of
each `TwinScenario`/`OrchestrationScenario` redeclaring its own
`system_stubs` from scratch.

US21.2 -- Explicit fault-injection catalog.
As an Automation Architect, I want a typed fault definition (timeout,
HTTP 500, HTTP 429, invalid response, slow response, missing data,
duplicate event, out-of-order event, permission denied, dependency
unavailable) attachable to any system in an environment with a
configurable probability -- most of these (duplicate/out-of-order event,
permission-denied, dependency-unavailable) have no equivalent today; only
a generic wrong-response substitution is possible via today's Static mode.

US21.3 -- Scenarios reference an environment, not inline config.
As an Automation Architect, I want a scenario to reference an environment
(and, optionally, a named fault set) by id/version rather than embedding
its own stub config, so a reusable scenario like "CRM Unavailable" is
defined once and usable across every agent that talks to CRM, instead of
copy-pasted per agent's scenario list.

US21.4 -- Synthetic dataset and persona management.
As an Automation Architect, I want named, versioned sample datasets (e.g.
`synthetic_customers`) and personas (customer, operations_user, compliance
officer) scoped to an environment, addressing the requirements' "systems /
datasets / personas / events" list beyond what today's per-scenario
`inputs: dict[str, Any]` provides.

## Notes / Open Questions

The existing Proxy/API/Static system-stub simulation modes (Epic 14's
Discovery section) remain the actual per-call simulation mechanism -- this
epic is about *where that configuration lives* (a reusable environment
instead of an inline scenario field) and *how faults are catalogued*
(an explicit typed list instead of only "static mode returns whatever you
configured"), not a replacement for the mechanism itself.

Migrating today's two scenario schemas (`TwinScenarioCreate.system_stubs`,
`OrchestrationScenarioCreate.system_stubs`) onto environment references
is a breaking schema change for anything already using inline stubs --
needs either a migration or an additive "environment_id overrides inline
stubs when set" compatibility path; not decided here.

API mode (a real dev-provided sandbox endpoint) stays out of scope, same
boundary as Epic 14 and Epic 20 already drew.
