# Epic 20 -- Agent Definition Governance: Permissions, Guardrails, Escalation & Tool Gateway Enforcement

**Status: US20.1/US20.2 fully implemented and enforced; US20.3/US20.4/
US20.5/US20.6 declared in the schema with real generation-time defaults
but not yet consumed by the runtime beyond what US20.1/20.2 need; US20.7
partially covered.** Groomed 2026-09-28, built 2026-09-29 --
`app/schemas/agents.py` (ResourcePermission, RuntimeGuardrails,
EscalationPolicy, ToolContract, ModelPolicy, KnowledgeSourceRef, all
nested on `AgentDefinition`; `AgentGovernanceUpdate` for editing an
already-generated artifact), `app/twin/gateway.py` (new -- the Tool
Gateway/Policy Engine: `check_tool_permission`, `GuardrailTracker`),
`app/twin/engine.py` (wired into `run_scenario`'s turn loop -- both Epic
14 and Epic 16 runtimes, since Epic 16 calls the same `run_scenario`),
`app/agents/generation.py` (populates real, non-placeholder defaults --
read-only tool contracts/permissions per declared tool, `RuntimeGuardrails()`
defaults, a starting `EscalationPolicy`, `ModelPolicy` mirroring the
existing model field), `app/api/agents.py`/`app/db/repository.py` (the
governance PATCH endpoint), `frontend/src/components/AgentDetailPanel.tsx`
(read-only governance summary). Live-verified: full backend suite (307
tests, up from 283) run against a real Postgres test DB in a container
built from this repo's own `axyntro-api` image (not a mocked DB), plus a
clean `tsc -b` + `vite build` + 93-test `vitest` pass on the frontend --
see `planning/decision-log.md`'s 2026-09-29 entry.

**Governance is opt-in per artifact, by design** (see `app/twin/
gateway.py`'s module docstring): an artifact with an empty
`tool_contracts` list -- every artifact generated before this epic, and
every hand-built test fixture that never sets the new fields -- behaves
exactly as it did under Epic 14/16 alone. This was a deliberate
backward-compatibility decision, not an oversight -- see the decision log
for why a fail-closed-by-default design was rejected.

**What's real vs. declared-only, precisely:**
- US20.1 (permissions) and US20.2 (guardrails) are both actually enforced
  -- a denied tool call is fed back to the model as a policy observation
  and downgrades the run to "failed"; exceeding any guardrail (steps, tool
  calls, wall-clock time, model calls, tokens, cost, or a simple
  loop-detection retry ceiling) stops the run with status "error", the
  same outcome the old max-turns-only check already produced.
- US20.6 (model policy) is populated with real values (capability,
  provider, temperature, fallback flag) but `AgentDefinition.model` stays
  the field `engine.py` actually calls the LLM with -- `model_policy` is
  additive descriptive metadata this pass, not a capability-based
  selection/fallback engine (that remains explicitly out of scope, same
  boundary Epic 12 already drew).
- US20.3 (escalation) has a real, non-empty default `escalate_when` +
  `escalation_target` on every generated artifact, but nothing in the
  runtime yet *acts* on it as a distinct outcome -- a guardrail breach
  today still becomes status "error", not a separate escalated state.
  That distinction needs Epic 17's run-state machine (a real
  `ESCALATED`/`WAITING_FOR_HUMAN` state) to mean anything, which is why
  Epic 17 was sequenced right after this one, not before.
- US20.4 (tool contracts split from adapters) has the schema split
  (`ToolContract.sandbox_mode: proxy|static|api`) and the Tool Gateway
  enforces the `(resource, action)` half of it, but `sandbox_mode` itself
  isn't yet wired to anything -- `app/twin/engine.py` still resolves a
  call's actual simulation mode from `scenario.system_stubs` (Epic 14's
  original per-scenario mechanism), not from the contract. Unifying those
  two is left for Epic 21 (environments), where system-stub config is
  already slated to move out of the scenario anyway.
- US20.5 (versioned knowledge sources) is schema-only -- `KnowledgeSourceRef`
  exists on the definition, but nothing generates, resolves, or records
  which version was in effect during a run (that's Epic 22's job).
- US20.7 (explicit isolation guarantees): tool allowlisting and
  authorization-not-by-prompt are real (the gateway itself). No
  production credential/network access by default was already true of
  Epic 14/16's design, not new here. Sensitive-data masking in recorded
  traces was **not** built this pass -- a denied call's `reason` string or
  a tool's raw arguments could still contain whatever the scenario/agent
  put there. Flagging this explicitly as an open gap rather than silently
  shipping partial coverage under a "done" checkbox.

Original groomed status (superseded by the above): **not started.**
Groomed 2026-09-28 from the "Digital Twin Agent Simulation" requirements
doc (Section 3's FR-AG-04 through FR-AG-10, and Section 9's FR-SEC-01)
against the current codebase -- see `planning/decision-log.md`'s
2026-09-28 entry.

Goal: Extend Epic 12's agent artifact with the governance fields the
requirements demand -- permissions, runtime guardrails, escalation rules,
versioned knowledge sources, tool contracts split from their environment
adapter, human-approval policy, and model policy -- and add a real
enforcement layer (a Tool Gateway + Policy Engine) so these are actually
checked by the runtime, not left as descriptive metadata an LLM prompt
might or might not honor. This is the largest schema-and-runtime change
among the epics groomed alongside it (17-23) and several of them (17's
`POLICY_CHECK` step, 21, 23's validation checklist) assume its fields
exist -- sequence it first among 17-23 if building in order.

Depends on: Epic 12 (`AgentArtifact`, the artifact this extends -- today
`tools_systems_needed` is a plain list of strings with no schema, and there
is no permissions, limits, escalation, or knowledge-source field at all).
Soft-depends on Epic 9/10 (`US9.9`/`US10.4`, the existing viewer/editor
role model) only in that this is explicitly a *different* kind of
permission -- what a given agent's tool calls are allowed to do -- not a
replacement for user-level product RBAC.
Feeds: Epic 17 (its `POLICY_CHECK` step records this epic's enforcement
decisions), Epic 14/16 runtimes (both should call through the same
gateway), Epic 21 (tool contracts here are what an environment's adapters
implement), Epic 22 (snapshots record policy/knowledge versions from
here), Epic 23 (its validation checklist checks these fields are present).

## User Stories

US20.1 -- Structured permissions block, enforced.
As a Platform Engineer, I want each artifact to declare per-resource
permissions (e.g. `customer.read`, `application.update`,
`compliance.override`), checked by a Tool Gateway before a tool call is
simulated (and, later, before a real one executes), so authorization never
relies solely on the system prompt telling the agent what it may do.

US20.2 -- Runtime guardrails and limits.
As a Platform Engineer, I want each artifact to declare `max_steps`,
`max_tool_calls`, `max_runtime_seconds`, `max_model_calls`, `max_tokens`,
`max_cost`, `retry.max_attempts`, and `loop_detection`, enforced by the
runtime -- terminating, failing, or escalating a run when exceeded -- in
place of today's single hardcoded `max_turns` cap
(`app/twin/engine.py`'s only limit today).

US20.3 -- Escalation rules as artifact data.
As an Automation Architect, I want `escalate_when` conditions and an
`escalation_target` (type + role) declared on the artifact itself, so a
run's escalation (Epic 17's `ESCALATION` state) is driven by explicit,
inspectable rules rather than only surfacing as an ad hoc deviation reason
after the fact.

US20.4 -- Tool contracts split from environment adapters.
As a Platform Engineer, I want each tool's logical contract (schema,
description, auth requirements, timeout, retry policy, side-effect
classification) kept separate from its sandbox vs. production
implementation, so Epic 14's existing proxy/static/API simulation modes
become a declared adapter *of* the tool contract rather than
scenario-only configuration with no artifact-level home.

US20.5 -- Versioned knowledge sources.
As an Automation Architect, I want an artifact to declare its knowledge
sources with explicit versions (e.g. `kyc_policy@17`), and every run to
record which versions were actually in effect, closing today's gap where
knowledge/RAG context has no versioned identity anywhere in the schema.

US20.6 -- Model policy on the artifact.
As a Platform Engineer, I want an artifact to declare a model capability
tag, preferred provider/model, temperature, and fallback-enabled flag,
building on Epic 12's existing single "model choice" field so a definition
isn't unnecessarily coupled to one hardcoded provider.

US20.7 -- Explicit, checkable sandbox isolation.
As a Platform Engineer, I want the guarantees the requirements list --
no production credentials or network access by default, secret isolation,
sensitive-data masking in recorded traces, an immutable audit trail, and
tool allowlisting -- enforced by the same Tool Gateway as US20.1, so
"the sandbox is isolated" is a checked property, not just a stated
intent.

## Notes / Open Questions

Grounded in the real gap found while scoping this: `app/twin/engine.py`
enforces exactly one limit (`max_turns`) today, and Epic 12's
`tools_systems_needed` remains plain strings with no permissions or
contract attached (Epic 14's `InferredToolSchema` synthesizes a *callable*
shape per scenario at simulation time, but nothing about permission or
side-effect classification lives on the artifact itself).

This is schema-plus-enforcement, deliberately bundled rather than split
into a separate "schema" epic and "enforcement" epic -- the fields are only
meaningful once something actually checks them, and building the schema
without the gateway risks repeating Epic 12's own pattern of descriptive-
only metadata this epic exists to fix.

API mode (a real dev-provided sandbox endpoint per tool) remains out of
scope here, same boundary Epic 14 already drew -- US20.4 only asks that the
contract/adapter *split* exist, not that a new adapter type be built.
