# Backlog (Deferred — Phase 2)

These are explicitly out of scope for the initial epics (1–10) but captured
now per product direction. Not yet broken into user stories at the same
level of detail — high-level intent only, to be groomed when this phase is
picked up.

## B1 — Agent Generation & Registry Publishing

Once the agentic blueprint (Epic 7/8) has identified and specified agents for
each automatable step, generate deployable agent artifacts and publish them.

Likely scope:
- Generate agent configuration from a blueprint node's spec (purpose, inputs,
  outputs, tools/systems, guardrails) — e.g. a provider-specific managed-agent config,
  or a portable agent-definition format (prompt + tool schema + model
  choice).
- "Generate agent" action per node (or per consolidated group of nodes) from
  the blueprint UI.
- Push generated agent artifact(s) to a registry (see B2).
- Track publish status / version per agent per blueprint node (draft →
  generated → published → deployed).
- Regeneration when the underlying blueprint node or baseline diagram
  changes (staleness detection).

## B2 — Agent Registry Connectivity

Ability to connect this product to one or more agent registries so generated
agents can be published to, and looked up from, a real registry rather than
just stored locally.

Likely scope:
- Registry connection configuration (endpoint, auth/credentials) — local
  config only for now, no assumption of a specific registry product yet.
- Support for connecting to multiple registries (pluggable connector
  interface), since the target registry product is not yet decided.
- Browse/search existing registered agents from within the product, to avoid
  regenerating an agent that already exists for an equivalent process step.
- Push/pull sync status and error handling (registry unreachable, auth
  failure, conflict on publish).

*Open question to resolve before scoping in detail: which registry
product/standard is the target (internal registry, a specific vendor, an
open agent-registry standard)? This determines the connector shape.*

## B3 — Digital Twin Test Environment

A simulated environment to test a generated agent blueprint end-to-end
against representative process runs before trusting it in production.

Likely scope:
- Ability to define test scenarios / synthetic cases for a process (sample
  inputs at the process's start event, expected path through gateways).
- Execute the generated agent(s) against those scenarios in an isolated
  ("digital twin") environment — not touching real systems.
- Compare agent-executed run vs. the as-is process baseline: time taken,
  error/exception rate, steps where the agent deviated or couldn't proceed
  (fell back to human), and rough cost per run (LLM API usage).
- Surface results per scenario and in aggregate, feeding back into blueprint
  confidence (e.g., "this agent passed 8/10 scenarios — investigate the 2
  failures before publishing to the registry").

*This is the most speculative backlog item and will need its own discovery
pass (what "digital twin" means concretely for this product) before writing
epics/stories.*
