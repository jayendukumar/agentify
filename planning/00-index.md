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
| 16 | [Orchestration Rehearsal](epics/16-orchestration-rehearsal.md) | Run an entire blueprint's generated agents together, end to end along the real process flow |

Suggested order: Epic 12 and Epic 13 can be built in parallel (neither
depends on the other). Epic 14 depends only on Epic 12 and is recommended
*before* Epic 15, so agents are twin-tested before they reach a shared
registry, even though the original backlog listed digital-twin testing
(B3) last. Epic 15 depends on both 12 and 13. Epic 16 depends on Epic 7,
12, and 14 (it runs Epic 14's engine per node, walking the finalized
process graph, rather than duplicating it).

## Phase 3 epics (groomed 2026-09-28 from the "Digital Twin Agent
Simulation" requirements doc)

Epics 12-16 above already deliver a working, if first-slice, digital twin:
agent artifacts (12), a sandboxed execution engine with scenario-based
grading (14), and multi-agent process rehearsal (16). A separate,
much more detailed requirements document ("Digital Twin Agent Simulation")
was reviewed against that existing implementation and groomed into seven
further epics -- it describes the fuller run-lifecycle model, observability
UI, evaluation/regression tooling, governance, environment/fault
management, reproducibility, and release-gate lifecycle that today's
first slice doesn't yet have, rather than a from-scratch rebuild of
"digital twin" itself:

| # | Epic | Summary |
|---|---|---|
| 17 | [Simulation Run Lifecycle & Execution Trace Model](epics/17-simulation-run-lifecycle.md) | Unify Epic 14/16's run states and trace shapes into one state machine + typed step model; unblocks manual human-in-the-loop pause/resume |
| 18 | [Live Observability: Synchronized Views, Inspector & Replay](epics/18-live-observability.md) | Three-pane Process/Agent/Technical views, step inspector, canvas highlighting, replay/time-travel |
| 19 | [Evaluation Dimensions, Regression Suites & Version Comparison](epics/19-evaluation-and-regression.md) | Named evaluation dimensions beyond pass/fail, reusable regression suites, side-by-side version comparison |
| 20 | [Agent Definition Governance](epics/20-agent-definition-governance.md) | Permissions, runtime guardrails, escalation rules, versioned knowledge sources, tool-contract/adapter split, model policy -- plus a Tool Gateway/Policy Engine that actually enforces them |
| 21 | [Digital Twin Environments & Fault Injection Catalog](epics/21-twin-environments-and-fault-injection.md) | Named/versioned reusable environments and datasets; the full fault-injection catalog (timeout, 5xx/429, duplicate/out-of-order event, permission-denied, dependency-unavailable, ...) |
| 22 | [Reproducibility: Execution Snapshots](epics/22-reproducibility-snapshots.md) | Every run records the exact versions of everything that influenced it, and that configuration can be reconstructed |
| 23 | [Agent Definition Quality Gate & Unified Release Lifecycle](epics/23-quality-gate-and-release-lifecycle.md) | DRAFT -> ... -> DEPLOYED checklist-gated lifecycle, reconciled with Epic 12/15's existing status fields |

Suggested order: Epic 20 first (17, 21, and 23 all assume its fields
exist) -- **done**, then Epic 17 (unified run/step model) -- **done**,
then 18/19/21 roughly in parallel (each depends on 17 but not on each
other) -- **18 done, 19 done, 21 done**, then 22 last (it snapshots
versions the others introduce) -- **done**, then 23 (depends on 20, 19,
and reconciling with 12/15) -- **done**. All of 17-23 are now built.
Epic 19's Notes section explicitly declines to scope the requirements
doc's "automated agent improvement loop" (its Section 12) as a committed
epic -- the source document itself frames that as future-looking, not
current scope.

## Phase 4 epics (groomed 2026-10-09 from a business-user UX review of the Digital Twin Simulation tab)

Epics 14-23 built a capable twin, but defining a test still means
hand-writing six JSON fields -- usable by an Automation Architect, not by
the Process Analyst / Process Owner who know what a realistic case looks
like. These epics put a guided, blueprint-driven flow in front of the
same engine.

| # | Epic | Summary |
|---|---|---|
| 24 | [Guided Digital Twin Test Builder](epics/24-guided-twin-test-builder.md) | Wizard + LLM-drafted, blueprint-grounded "story card" situations; plain-language system/reviewer behaviour; pre-filled expectations; coverage meter; JSON form kept as Advanced |
| 25 | [Test Data Kitchen](epics/25-test-data-kitchen.md) | Sample packs (typical/edge/messy), describe-it generation, "make it tricky" mutations, clone & disguise, reviewer persona casting -- saved as environment datasets |
| 26 | [Business-Readable Twin Results & Tab Consolidation](epics/26-business-readable-twin-results.md) | Story-first results, "fix this" links, run-all, one "Test your agents" tab |

Suggested order: 24 and 25 together (25 is step 3 of 24's wizard), with
26's US26.1 shipping alongside; the rest of 26 after real usage.

## Phase 5 epics (groomed 2026-10-11 from the [Enterprise Context Strategy](enterprise-context-strategy.md))

Every LLM call today sees only the process in front of it -- nothing about
the organization's verticals, jurisdictions, applications, policies,
regulatory obligations, or approval authority. These epics add an
organization context layer and feed it into every stage, from
small-business onboarding up to federated, multi-vertical enterprises.

| # | Epic | Summary |
|---|---|---|
| 27 | [Organization Context Foundation](epics/27-organization-context-foundation.md) | Organization / business unit / jurisdiction scope on processes; profile wizard; roles and approval authority; policy-document ingestion into obligations/controls with human confirm; starter regulatory packs |
| 28 | [Context Resolver & Context Packs](epics/28-context-resolver-and-packs.md) | Applicability + filtered retrieval into versioned, citable, token-budgeted packs; wired into gap analysis, blueprint, agent generation, chat; staleness propagation |
| 29 | [Application & Data Catalog](epics/29-application-and-data-catalog.md) | Applications with aliases replace free-text system names; OpenAPI import and twin API mode; data classification driving permissions |
| 30 | [Compliance-Aware Testing & Release](epics/30-compliance-aware-testing-and-release.md) | Rule coverage in the test builder; reviewers from the approval matrix; control coverage in the release gate; compliance evidence export |
| 31 | [Enterprise Connectors & Federation](epics/31-enterprise-connectors-and-federation.md) | Job queue; MCP-based read-only connectors (policies, CMDB/EA, GRC, data catalog, IdP); per-unit stewardship and access; per-unit model/provider policy |

Suggested order: 27 then 28 (the minimum that changes outcomes -- works on
uploaded documents alone, so it serves small businesses immediately), then
29 and 30 in parallel, then 31 for mid-size and large organizations.

## Phase 6 epics (groomed 2026-10-11 from a ground-truth and volume-testing review)

Twin testing today is one scenario at a time: one input, one hand-typed
set of expectations, one run, judged as pass/fail. These epics let an
agent be tested against thousands of labelled cases. Ground truth comes
from historical outcomes, the process's own gateway rules, and
label-first generation rather than per-row typing. Runs repeat for
consistency, and the release gate decides on pass rates with known
confidence.

| # | Epic | Summary |
|---|---|---|
| 32 | [Labelled Datasets & Ground Truth Management](epics/32-labelled-datasets-and-ground-truth.md) | Rows with inputs plus expected labels; data-driven scenarios; historical CSV import with pseudonymization; label review, ambiguity, versioning, holdout; promote failures to golden cases |
| 33 | [Scalable Oracles](epics/33-scalable-oracles.md) | Structured gateway rules compiled into a rule oracle; label-first generation at volume; metamorphic relations; calibrated LLM judge for free text only; disagreement-driven review queue |
| 34 | [Batch Execution at Volume](epics/34-batch-execution-at-volume.md) | Durable job queue (pulls US31.1 forward); repeats per row; cost estimate and budget cap; provider throttling; prompt caching; seeded replay; tiered suites; scheduled runs; load profile |
| 35 | [Results at Volume & Statistical Release Gate](epics/35-results-at-volume-and-statistical-gate.md) | Pass rate with confidence; segment and decision breakdowns; failure clustering; flakiness; paired version comparison; statistical criteria in the Epic 23 gate; evidence export |

Suggested order: 32 first (US32.1-32.3 alone make volume testing
possible with historical data), then 34's US34.1-34.4 so batches actually
run, then 35's US35.1-35.4 so results are readable. 33 can start as soon
as 32 lands. Its rule oracle (US33.1-33.2) has the biggest payoff, since
it labels any number of rows for free.

## Phase 7 epic (groomed 2026-10-13 while building the multi-agent process demo)

Epics 14-35 prove one agent at a time. Building a 9-agent "Invoice to
Pay" demo showed the twin could not yet run a *whole process* over many
cases: gateways were fixed per scenario, hand-offs carried only the
previous agent's output, simulated systems were invented per call, and
nothing measured the to-be process against today's.

| # | Epic | Summary |
|---|---|---|
| 36 | [Process Digital Twin at Volume: Efficiency & Readiness](epics/36-process-twin-efficiency.md) | Sample-data-backed systems; case file hand-off; rule- and agent-routed gateways; as-is operating model; whole-process batches; efficiency report (STP, accuracy, cycle/touch time, cost, FTE) with confidence ranges; Process twin page. Still open: capacity/queueing simulation (US36.9), parallel gateways |

## Phase 8 epic (groomed 2026-10-14 from the enterprise-readiness ask)

Sign-in is a name and a self-chosen role with no password, the only
roles are viewer and editor, and whoever builds an agent can approve its
release. This epic puts the product behind the customer's IdP and
separates the duties.

| # | Epic | Summary |
|---|---|---|
| 37 | [Enterprise Identity, Roles & Segregation of Duties](epics/37-enterprise-identity-and-access.md) | Analyst/architect/process owner/approver/auditor/admin roles as permissions; route-guard completeness test; roles scoped by business unit (per-process scope kept open); contribution history; any amendment to a process or agent definition needs an independent approval, baselines included; optional two-person approval; audit log; OIDC + SAML SSO with MFA verified from claims; inbound SCIM with group-to-role mapping; 15-min sessions, rotating refresh tokens, admin revocation |

Status: build steps 1-4 implemented 2026-10-14 to 2026-10-15: roles,
segregation of duties, audit, the production guard and demo cast;
session hardening; OIDC + MFA (tested live against Keycloak); SCIM
provisioning and group mappings. Open: SAML (US37.9).

## Phase 9 epic (groomed 2026-10-15 for a hosted offering)

Every table, setting, file and job assumes one organization per
deployment. Hosting several customers needs isolation the database
enforces, identity per tenant, fair shares of the job queue, and a tenant
lifecycle with export and deletion.

| # | Epic | Summary |
|---|---|---|
| 38 | [Multi-Tenancy: Many Organizations on One Deployment](epics/38-multi-tenancy.md) | Tenants addressed by host name; tenant_id on every row with Postgres row-level security (fails closed); tenant context for requests, jobs and background work; backfill into a first tenant; a cross-tenant probe over every route; per-tenant IdP, SCIM and mappings; operators without standing data access; fair-share jobs and quotas; per-tenant secrets, files, model policy and registries; create, suspend, export and delete; dedicated database and data residency on demand |

Status: groomed 2026-10-15. Not started.

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
