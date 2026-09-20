# Epic 12 -- Agent Artifact Generation

**Status: all 6 stories implemented** (`backend/app/agents/`, `backend/app/api/agents.py`,
`AgentArtifactModel`; `frontend/src/components/BlueprintDetailPanel.tsx`).
Live-verified against the real dev Postgres DB and a real LLM-produced
blueprint (not just the test DB/fake LLM) -- see `planning/decision-log.md`'s
2026-09-20 Epic 12 entries, including a real dev-server reload defect found
and fixed during that verification.

Goal: Turn each automatable/partial blueprint node's agent spec (Epic 7) into
a concrete, portable agent artifact -- a real definition that could actually
be run or deployed -- rather than leaving the blueprint as a descriptive
proposal only.

Depends on: Epic 7 (per-node agent spec), Epic 8 (the UI surface this is
triggered from).
Feeds: Epic 14 (digital twin execution needs a generated artifact), Epic 15
(publishing needs a generated artifact to push).

## User Stories

US12.1 -- Generate agent artifact from a blueprint node.
As an Automation Architect, I want to trigger "Generate agent" on an
automatable or partial blueprint node, so that its proposed agent spec
becomes a concrete artifact I can inspect, test, and eventually deploy,
rather than just a descriptive record sitting in the blueprint overlay.

US12.2 -- Portable, provider-agnostic agent definition.
As an Automation Architect, I want the generated artifact expressed as a
portable definition (a system prompt derived from purpose/trigger/rationale,
a tool schema derived from `tools_systems_needed`, an I/O schema derived from
`required_inputs`/`expected_outputs`, and a model choice), so that it isn't
locked to one agent framework or vendor before that choice has been made --
consistent with how Epic 9's LLM layer stays provider-agnostic.

US12.3 -- Draft / generated / stale lifecycle state.
As an Automation Architect, I want each agent artifact to carry a lifecycle
state (draft, generated, stale), so I can tell at a glance whether it still
reflects the current blueprint or needs regenerating.

US12.4 -- Staleness detection on blueprint drift.
As an Automation Architect, I want an already-generated artifact
automatically flagged stale when its source blueprint node's spec changes
(an override, a node-level edit, or a full blueprint regeneration per
US7.7), so that I never test, deploy, or publish an agent built from an
outdated spec without knowing it.

US12.5 -- Consolidated-group generation.
As an Automation Architect, I want to generate a single artifact for a
consolidated group of nodes (per US7.5's grouping recommendation), so the
artifact boundary matches the actual automation design instead of being
forced one-artifact-per-node.

US12.6 -- Inspect and download the generated artifact.
As an Automation Architect, I want to view and download a generated
artifact's full definition (JSON/YAML), so I can review exactly what would
be deployed or published, and hand it to engineering directly even before
any registry integration exists.

## Notes / Open Questions

This epic consumes the agent spec schema from the `agentic-blueprint-
evaluator` skill (`name`, `purpose`, `trigger`, `required_inputs`,
`expected_outputs`, `tools_systems_needed`, `human_checkpoint`,
`consolidated_from_nodes`) as-is -- it should map that schema into a runnable
definition, not redesign it.

No target agent runtime/framework is chosen yet (a vendor Managed Agent, a
raw tool-calling loop, LangChain, etc.). This epic deliberately produces a
portable definition rather than betting on one; a provider-specific export
(the original backlog's "provider-specific managed-agent config" idea)
becomes a possible later export *target* from this same definition, not the
primary artifact format.

"Model choice" per artifact is a new field, not previously modeled anywhere.
Default it to whatever Epic 9's LLM layer currently uses; don't build a
model-selection engine here -- that's speculative scope beyond what's asked.
