# Agentic Solution Generator — Product Plan (High-Level)

**Status:** Draft v1
**Scope:** Epics and user stories only (no task breakdown / estimates yet).

## Product Summary

Ingest business process documentation (PDF, DOCX, Visio, images), extract the
underlying business process, generate an **as-is BPMN 2.0 diagram**, let users
refine it via a **chat interface** (add/amend/delete nodes and relationships),
**finalize** it as a baseline, then generate an **agentic blueprint overlay**
that evaluates every step for AI-agent automation feasibility — showing which
steps can be automated by an agent (and what that agent needs), and which
cannot.

Phase 2 (backlog, not yet scoped in detail): generate deployable agents and
publish them to a registry, connect to external agent registries, and build a
"digital twin" test/simulation environment.

## Target Stack (constraint, not yet built)

- **UI:** Node.js / React
- **Backend:** Python (API layer for the UI)
- **Relational DB:** configuration, process metadata, versions, users
- **Vector DB:** embeddings of ingested document chunks (retrieval/context for
  extraction and chat)
- **Document store:** original uploaded files + derived artifacts (extracted
  text, generated BPMN XML, blueprint reports)
- **Target environment:** local development and local testing (no cloud
  deployment assumed yet)
- **LLM:** OpenRouter routing to Qwen3.7 Flash (`qwen/qwen3.7-flash`) --
  chosen for cost, current as of 2026-09-17; see
  [claude-api-access-notes.md](claude-api-access-notes.md) for the
  provider-billing background and comparison this decision was made from

## Personas

| Persona | Role in the product |
|---|---|
| Process Analyst / Business Analyst | Uploads source documentation, reviews/edits the as-is diagram via chat, finalizes the baseline |
| Automation Architect / COE Lead | Reviews the agentic blueprint, validates agent selections, overrides recommendations |
| Business Stakeholder / Process Owner | Views diagrams and blueprint summaries, less hands-on editing |
| Platform Engineer / Developer | Builds and runs the system locally, manages the local stack, config, and LLM API usage |

## Epics

| # | Epic | Summary |
|---|---|---|
| 1 | [Document Ingestion Pipeline](epics/01-document-ingestion.md) | Accept and parse PDF / DOCX / Visio / image sources into raw extracted content |
| 2 | [Business Process Knowledge Store](epics/02-knowledge-store.md) | Persist structured process data, embeddings, and source documents |
| 3 | [As-Is BPMN Generation Engine](epics/03-bpmn-generation.md) | LLM-driven generation of valid BPMN 2.0 XML from extracted process info |
| 4 | [Process Diagram UI](epics/04-diagram-ui.md) | Interactive canvas rendering and direct manipulation of the BPMN diagram |
| 5 | [Conversational Diagram Editing](epics/05-chat-editing.md) | Chat interface to add/amend/delete nodes and relationships |
| 6 | [Diagram Finalization & Versioning](epics/06-finalization-versioning.md) | Lock a reviewed baseline, version history, diagram diffing |
| 7 | [Agentic Blueprint Generation Engine](epics/07-agentic-blueprint-engine.md) | Per-node evaluation of agent-automation feasibility |
| 8 | [Agentic Blueprint Visualization](epics/08-blueprint-visualization.md) | Interactive, explainable presentation of the blueprint overlay |
| 9 | [Platform Foundations](epics/09-platform-foundations.md) | Backend/API/frontend scaffolding, DBs, local dev stack |
| 10 | [Cross-Cutting / Non-Functional](epics/10-nonfunctional.md) | Logging, cost tracking, access control, data privacy |
| 11 | [Process Gap Analysis & Clarification](epics/11-gap-analysis-and-clarification.md) | Detect extraction gaps/ambiguities and resolve them via user clarification before diagram generation |

## Phase 2 epics (groomed from the backlog, not yet built)

Groomed 2026-09-20 from `backlog.md`'s B1/B2/B3 into four epics -- see each
epic's own Notes section for the reasoning behind splitting/resequencing
them relative to the original backlog items:

| # | Epic | Summary |
|---|---|---|
| 12 | [Agent Artifact Generation](epics/12-agent-artifact-generation.md) | Turn a blueprint node's agent spec into a concrete, portable, deployable artifact |
| 13 | [Agent Registry Connectivity](epics/13-agent-registry-connectivity.md) | Pluggable registry connector, local reference registry, browse/search |
| 14 | [Digital Twin Simulation & Validation](epics/14-digital-twin-simulation.md) | Test a generated agent against synthetic scenarios in an isolated sandbox before it's trusted |
| 15 | [Agent Publishing & Lifecycle Management](epics/15-agent-publishing-lifecycle.md) | Publish a generated artifact to a registry and track its status over time |

Suggested order: Epic 12 and Epic 13 can be built in parallel (neither
depends on the other). Epic 14 depends only on Epic 12 and is recommended
*before* Epic 15, so agents are twin-tested before they reach a shared
registry, even though the original backlog listed digital-twin testing
(B3) last. Epic 15 depends on both 12 and 13.

## Backlog (superseded by the Phase 2 epics above)

See [backlog.md](backlog.md) -- kept as the historical record of the
original, ungroomed ask:

- **B1 — Agent Generation & Registry Publishing**
- **B2 — Agent Registry Connectivity**
- **B3 — Digital Twin Test Environment**

## Key Decisions

Standalone decision records, cross-linked from the epics/stories they affect:

| Decision | Summary |
|---|---|
| [claude-api-access-notes.md](claude-api-access-notes.md) | LLM provider: OpenRouter routing to Qwen3.7 Flash, chosen for cost (Epic 9, US9.3) |
| [document-ingestion-strategy.md](document-ingestion-strategy.md) | Document extraction sizing (simple single-call now, chunking as a fast-follow) and embedding strategy (local model, deferred to Epic 2) (Epic 1 US1.3/US1.8, Epic 2 US2.3) |

These are single-topic decisions. For the running, chronological record of
*every* non-obvious trade-off and defect found while implementing (not
just the headline ones above), see
[decision-log.md](decision-log.md) -- maintained continuously per the
`decision-log` skill, not written up after the fact.

## Suggested High-Level Sequencing

1. Epic 9 (foundations) bootstrapped in parallel with Epic 1–2 (ingestion +
   storage) since nothing else works without them.
2. Epic 3 (BPMN generation) depends on Epic 1–2.
3. Epic 4 (diagram UI) can start against mock/sample BPMN while Epic 3 matures.
4. Epic 5 (chat editing) depends on Epic 3 + 4.
5. Epic 6 (finalization) depends on Epic 4 + 5.
6. Epic 7 (blueprint engine) depends on Epic 6 (needs a finalized baseline).
7. Epic 8 (blueprint visualization) depends on Epic 7.
8. Epic 10 (non-functional) threads through all epics but should be seeded
   early (logging, config/secrets handling) rather than bolted on at the end.
