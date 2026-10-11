# Epic 28 -- Context Resolver & Context Packs

**Status: implemented (2026-10-11).** Groomed 2026-10-11 from
[enterprise-context-strategy.md](../enterprise-context-strategy.md).
Built in `app/context/resolver.py` (applicability, filtered pgvector
retrieval, per-purpose token budgets, content-addressed packs, citation
mapping, staleness) and `app/context/consumers.py` (blueprint human-review
guard, agent enrichment, change impact, suite re-runs), wired into gap
analysis, blueprint evaluation, agent generation, BPMN chat, and document
extraction; migration `f8a9b0c1d2e3`; frontend "Applicable rules",
"Why we're asking", the pack inspector, staleness banner, and the
"What uses this?" impact view. Live-verified against OpenRouter -> Qwen
(see the decision log, 2026-10-11).

Goal: turn the organization context store (Epic 27) into the right,
bounded, citable slice of context for each LLM call. A **Context Pack** is
built from a process's scope, versioned, pinned on everything produced
from it, and fed into every LLM call site. This is the epic where context
starts changing outcomes.

Depends on: Epic 27. Feeds: every LLM call site, Epic 22 (snapshots),
Epic 30.

## Design (decided in the strategy doc)

1. **Deterministic applicability first.** Which regulations, controls,
   roles, and applications apply comes from structured `applies_to` edges
   matched against the process scope (business unit, jurisdictions,
   systems touched, data classes, step types).
2. **Semantic retrieval second.** Within the applicable set, pgvector
   search (filtered by the same scope metadata) picks the relevant policy
   clauses for the specific call.
3. **Token budget per call site.** Each consumer declares a budget. The
   resolver ranks and trims to fit, preferring structured facts (limits,
   roles) over prose.

## User Stories

US28.1 -- Context resolver service.
As a Platform Engineer, I want one resolver (`app/context/resolver.py`)
that takes a process scope plus a call-site purpose and returns a Context
Pack (applicable entities, retrieved clauses, glossary hits), so that call
sites never query the context store directly.

US28.2 -- Versioned, pinned packs.
As an Automation Architect, I want each Context Pack to record exactly
which entity and clause versions it contained, and blueprint overlays,
agent artifacts, and run snapshots (Epic 22) to pin the pack version they
were produced with, so I can always answer "which version of this policy
was this agent built and tested against".

US28.3 -- Citation contract.
As a Compliance Lead, I want every context-influenced output (a gap
question, a blueprint verdict, a checkpoint, an agent's knowledge sources)
to carry citations to the pack entries it relied on, so outputs are
defensible and reviewable. This follows the existing `source_refs`
convention.

US28.4 -- Context in gap analysis.
As a Process Analyst, I want gap analysis to raise policy-grounded findings
("no step records consent -- required by PRIV-002"), each with a "why we're
asking" citation.

US28.5 -- Context in blueprint evaluation.
As an Automation Architect, I want blueprint evaluation's rubric factor 4
(authority/liability cost) judged against the applicable controls, so a
step a policy says needs human review is never marked fully automatable.
Each step should show an "Applicable rules" section with citations.

US28.6 -- Context in agent generation.
As an Automation Architect, I want generated agents to carry real
escalation targets (named roles), guardrails required by applicable
controls (e.g. decision logging for high-risk AI uses), and
`knowledge_sources` populated with the pinned policy versions, replacing
today's generic defaults.

US28.7 -- Context in BPMN chat and extraction.
As a Process Analyst, I want extraction and chat edits to resolve the
organization's vocabulary and application names through the glossary, so
"the onboarding system" maps to the right thing.

US28.8 -- Staleness propagation.
As a Process Owner, I want a change to a context entity (a new policy
version, a changed approval limit) to flag the blueprints and agents whose
pinned pack contained it as stale, and to re-run their regression suites
(Epic 19), so policy changes don't silently diverge from what was tested.
This reuses Epic 12's `stale` status and Epic 19's suite mechanism.

US28.9 -- Pack inspector.
As an Automation Architect, I want to see the exact Context Pack any
output was produced with (entities, clauses, token count), so I can
diagnose why the model decided what it did.

## Notes / Open Questions

- **Over-retrieval is the main quality risk.** Packing every applicable
  policy into every prompt raises cost and lowers quality. Budgets are
  mandatory, and relevance should be measured on real processes before any
  budget is raised.
- **Prompt caching:** a pack is stable across many calls for one process
  (e.g. every node of a blueprint), so put it in the cacheable prompt
  prefix.
- **Latest vs. pinned:** new outputs use the latest context. Existing
  outputs keep their pinned pack until a user explicitly re-baselines.
  Staleness (US28.8) makes the difference visible rather than silently
  upgrading.
- Wiring into the twin's tool schemas and test builder is deliberately left
  to Epics 29 and 30, which add the catalog and control data those need.
