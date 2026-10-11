# Enterprise Context Strategy

**Status:** Proposal (2026-10-11). Groomed into Epics 27-31 -- see
"Proposed epics" at the end; full epics are `planning/epics/27-*.md` to `31-*.md`.

## The problem: every LLM call is guessing about the organization

Today every model call in the pipeline sees exactly one thing: the process
being worked on (its documents, schema, BPMN, blueprint). It knows nothing
about the organization that owns that process. The gaps are concrete:

| Call site | What it guesses today | What it could be told instead |
|---|---|---|
| Extraction / gap analysis (`app/ingestion`, `app/gap_analysis`) | What "the CRM" or "the approval" means | The org's glossary, application inventory, approval matrix |
| Blueprint evaluation (`app/blueprint/prompts.py`) | Rubric factor 4, "authority/liability cost only an accountable person can bear" -- judged with no policy knowledge | The regulations and internal controls that actually apply to this step |
| Agent generation (`app/agents/generation.py`) | Default permissions, guardrails, escalation targets, `knowledge_sources: []` | Real role/entitlement model, data classification, named escalation roles, the policies the agent must follow |
| Twin tool schemas (`app/twin/schema_inference.py`) | Invents an API for a free-text name like "CRM system" | The real system's API spec, owner, data classes, environments |
| Twin test builder (`app/twin/test_builder.py`) | Situations and test data from the agent's fields alone | Compliance-driven situations ("sanctions potential match"), data in the org's real formats, reviewer behaviour from the actual delegation-of-authority limits |

Two structural facts make this a real architectural change, not a prompt tweak:

1. **There is no organization in the data model.** Everything hangs off
   `process_id` (`app/db/models.py`). Users carry only a role. Context is
   by nature *cross-process* and *scoped by business unit and
   jurisdiction*. Nothing today can express either.
2. **Systems are free-text strings everywhere** (`tools_systems_needed:
   list[str]`). The same application appears as "CRM", "CRM system", and
   "Salesforce" across processes. Nothing links them, so nothing can
   attach a real schema, owner, or data classification to them.

A useful existing asset: `document_embeddings` (pgvector, Epic 2 US2.3) is
populated but **never queried**. Retrieval infrastructure exists. It has
simply never had a reason to be used.

## Worked example: a multi-vertical regulated group

*Meridian Group* (fictional): about 40,000 staff and three verticals, each
with a different regulatory surface:

| Vertical | Jurisdictions | Example obligations | Example systems |
|---|---|---|---|
| Retail Banking | EU, UK | AML directives & sanctions screening, GDPR (incl. Art. 22 automated decisions), EU AI Act (creditworthiness = high-risk), DORA (ICT third-party register) | Core banking platform, KYC screening service, CRM |
| Insurance | US (multi-state) | State insurance regulations, privacy rules, claims-handling fairness | Policy admin system, claims platform |
| Benefits Administration | US | Health-privacy rules, benefits-plan rules | HRIS, benefits platform, payroll |

Plus group-wide internal policy: an information-security policy, a data
classification scheme, a delegation-of-authority (DoA) matrix, a model-risk
policy for AI, and a vendor-management policy.

**One process, "Customer Onboarding", modelled for Retail Banking EU.**
The table below shows what changes once context is available:

| Pipeline stage | Without context | With context |
|---|---|---|
| Gap analysis | Asks generic questions | Flags *"No step records consent for data processing -- required by internal policy PRIV-002"* and *"Sanctions screening has no documented escalation path -- required by AML-004 §3"* |
| Blueprint: "Screen against sanctions lists" | `automatable` (it looks like a data lookup) | `partial`. Potential matches must be reviewed by a human per AML-004, so `human_checkpoint: review_before_action` with the escalation target resolved to the **MLRO** role. The rationale cites the policy clause. |
| Blueprint: "Assess credit limit" | `automatable` (rule-based) | `partial`, flagged as **high-risk AI use**: human oversight, decision logging, and an explanation requirement become required guardrails, citing GDPR Art. 22 / EU AI Act |
| Agent generation | `permissions` default-granted on "Core banking" | Read-only on Customer and Account, deny on Payments, from the entitlement model. Data class *Confidential-PII*, EU residency. `knowledge_sources` = [AML-004 v7, PRIV-002 v3] |
| Twin tool schemas | Invented "core_banking_lookup" | The real API spec imported from the API gateway, so the twin's stubs match production shapes |
| Twin test builder | Generic happy path / system down | Coverage targets now include **controls**: "potential sanctions match is escalated", "missing consent blocks onboarding", "limit above €50k needs Head of Ops" (from the DoA matrix, which becomes a threshold reviewer persona automatically). Test data uses EU formats (IBANs, national ID shapes) and synthetic sanctions near-matches. |
| Release (Epic 23 gate) | Checklist of governance fields | Checklist adds "every applicable control has a passing twin situation", giving per-obligation **compliance evidence** |

The **same process name in the Insurance (US) vertical** resolves to a
different context slice: different regulations, different systems, a
different DoA chain. That is why context must be *scoped*, not global.

## Target architecture: an Organization Context layer

```
                ┌──────────────────────── Sources ────────────────────────┐
  Tier 0 (SMB)  │ Uploaded policy docs · org profile questionnaire · CSVs │
  Tier 1 (Mid)  │ SharePoint/Confluence · ITSM CMDB · IdP groups · OpenAPI│
  Tier 2 (Ent.) │ GRC platform · EA tool · data catalog · MCP connectors  │
                └───────────────────────────┬─────────────────────────────┘
                                            ▼
                     Ingestion & normalization (reuses Epic 1 extractors,
                     LLM structuring, human confirm -- like gap findings)
                                            ▼
   ┌──────────────────────── Organization Context Store ────────────────────────┐
   │ Entities (Postgres, versioned):                                            │
   │  Organization ─ BusinessUnit/Vertical ─ LegalEntity/Jurisdiction            │
   │  Application/System ─ API spec ─ DataAsset ─ DataClassification             │
   │  Role ─ ApprovalAuthority (DoA thresholds) ─ Owner                          │
   │  Regulation ─ Obligation ─ Control ─ Policy (+ clause text)                 │
   │  Glossary term                                                             │
   │ Edges: applies_to(vertical|jurisdiction|data_class|process_type|system),   │
   │        owned_by, implements(control→obligation), processes(system→data)    │
   │ Passages: policy/regulation text chunks in pgvector, with metadata          │
   └──────────────────────────────────┬─────────────────────────────────────────┘
                                      ▼
            Context Resolver -- given a process's scope (vertical,
            jurisdictions, systems touched, data classes, step types):
              1. deterministic applicability over the graph
              2. semantic retrieval of passages, filtered by that scope
              3. token-budgeted, versioned **Context Pack** with citations
                                      ▼
   Consumers: gap analysis · BPMN chat · blueprint · agent generation ·
              twin tool schemas · test builder · release gate · run snapshots
```

### Key design choices

**1. Scope, not tenancy-first.** Add `organization` → `business_unit` →
`jurisdiction` as first-class entities, and give each process a *context
scope* (one BU, one or more jurisdictions). Every context entity carries
`applies_to` edges. A context pack is the result of resolving that scope.
Hard multi-tenancy (separate orgs on one deployment) is a later concern.
The local-first product needs *intra*-org scoping long before it needs
*inter*-org isolation.

**2. Deterministic applicability first, semantic retrieval second.**
"Does GDPR apply to this process?" should come from structured edges
(jurisdiction = EU, data class = personal). It should not depend on
whether a vector search happened to rank a GDPR chunk highly. Retrieval
then supplies the *relevant clauses* within the applicable set. This keeps
answers explainable and auditable, which regulated customers require.

**3. Context Packs are versioned and pinned.** A pack records exactly
which policy/control/system versions it contained. Blueprint overlays,
agent artifacts, and run snapshots (Epic 22) pin the pack version they
were produced with. That answers the auditor's question: *"which version
of the AML policy was this agent tested against?"* When a source changes,
the existing staleness pattern (Epic 12's `stale`, Epic 19's regression
suites) propagates: affected blueprints and agents are flagged stale, and
their regression suites re-run.

**4. Systems become catalog references, not strings.** Introduce an
`Application` entity with aliases, then resolve free-text system names
against it during extraction, with a confirm step for ambiguous matches.
Keep the string for backward compatibility and add an optional
`application_id`. Once a system resolves, the twin uses the real API spec
(the "API mode" Epic 14 deferred becomes reachable), permissions derive
from data classes, and DORA-style registers fall out for free.

**5. Citations everywhere.** Every context-influenced claim (a verdict
rationale, a checkpoint, a generated control situation) carries citations
to the pack entries it used. This uses the same pattern as `source_refs`
on process elements. It is also what makes the output defensible to a
compliance team.

**6. Humans confirm machine-extracted context.** Obligations and controls
extracted from policy PDFs are proposals until a named owner confirms
them. Reuse Epic 11's gap-finding confirm/dismiss UX. Large orgs need
*federated stewardship*: each vertical's compliance lead owns that
vertical's slice.

## Impact on the current architecture

| Area | Change | Size |
|---|---|---|
| Data model | New `context` module and tables (entities, edges, versions, passages). `processes` gains a scope. Optional `application_id` alongside system strings. Pack version pinned on overlays, artifacts, snapshots. | Large, additive |
| Retrieval | First real consumer of pgvector. Chunk metadata (vertical, jurisdiction, effective dates, classification) for filtered search. The 384-dim MiniLM model is likely too weak for regulatory text, so plan a model swap behind `app/ingestion/embeddings.py`. | Medium |
| Prompting | Each LLM call site gets a `context_pack` parameter and a citation contract in its JSON schema. Token budgets per call site. Prompt caching for the pack prefix (the pack is stable across many calls). | Medium, touches ~8 call sites |
| Background work | Connector syncs, re-embedding, and stale propagation outgrow FastAPI `BackgroundTasks`. Add a job queue (e.g. Arq or RQ on Postgres/Redis) with retries and schedules. | Medium |
| Security | Context is sensitive (internal policies, system topology, entitlements). Need BU-scoped access control on context, PII redaction before LLM calls, and an audit log of context reads. | Medium–Large |
| Model/provider policy | Regulated customers will not send policy text and system topology to an arbitrary hosted model. The decision log already flags the current Chinese-hosted default (2026-09-17). Need a per-organization (eventually per-BU) model/provider policy and a self-hosted option. Epic 20's `ModelPolicy` is the natural hook. | Medium |
| Release gate | Epic 23's checklist adds control coverage: each applicable control has at least one passing twin situation. | Small once the above exists |

## Enablement path: small business to large enterprise

The same context store serves every tier. What changes is **how context
gets in** and **who owns it**.

### Tier 0 -- Small business (≈10–200 people)
- **Org profile wizard** (5 minutes): industry, countries, size, top 10
  applications, who approves what above which amount. This alone yields
  jurisdictions, a starter application list, and a basic DoA matrix.
- **Starter regulatory packs** curated per industry × geography (e.g. "EU
  retail / GDPR basics", "US healthcare-adjacent / privacy basics"). These
  give obligations and plain-language controls, so the user doesn't need
  to upload regulations.
- **Upload policy documents** (employee handbook, expense policy) through
  the existing ingestion pipeline. The LLM extracts rules, thresholds,
  roles and terms, and the user confirms them.
- Single business unit, no connectors, everything on the existing stack.

### Tier 1 -- Mid-market (≈200–5,000)
- **Read-only connectors** for the systems these companies already have:
  - Policy libraries (SharePoint, Confluence).
  - Application inventory from the ITSM tool's CMDB.
  - Roles from the identity provider (Entra ID or Okta groups).
  - API specs from the API gateway or a repo of OpenAPI files.
- **Scheduled sync with change detection.** A policy update produces a new
  context version, which flags dependent blueprints and agents as stale.
- A few business units, with a context steward role per BU.

### Tier 2 -- Large, multi-vertical, regulated enterprise
- **System-of-record integrations**, read-only and never re-mastered here:
  - GRC platform for obligations, controls and mappings.
  - Enterprise-architecture tool for the application portfolio.
  - Data catalog for classifications and lineage.
  - IdP for entitlements.
- Machine-readable control catalogs where they exist (e.g. NIST OSCAL).
- **MCP as the connector interface**, so connectors are pluggable and can
  be built by the customer's own teams.
- **Federated stewardship and attribute-based access:** each vertical
  sees and owns its slice, while group-wide policies apply everywhere.
- Per-BU model/provider policy and data-residency enforcement.
- Context change → impact analysis: *"AML-004 v8 published — 3 blueprints,
  5 agents, 2 regression suites affected."*
- Scale notes:
  - Postgres plus pgvector is sufficient into the low millions of passages.
  - A dedicated graph database only earns its place if applicability
    queries get deep (multi-hop control inheritance across legal
    entities). Defer it.

## What the user sees (business-friendly, consistent with Epics 24–26)

- **Process setup:** "Which part of the business is this?" (vertical +
  countries), asked once, so the scope is set.
- **Gap analysis:** policy-grounded questions, each with a "why we're
  asking" citation.
- **Blueprint step panel:** an **"Applicable rules"** section listing the
  obligations and controls behind each verdict, with clickable citations.
- **Agent card:** "Follows: AML-004 v7, PRIV-002 v3 · Data: Confidential-PII
  (EU)".
- **Twin coverage meter:** a third category, **"Rules"**, next to paths and
  failures, with one chip per applicable control.
- **Release:** a "Compliance evidence" view mapping each control to its
  passing situations.

## Risks and open questions

- **Over-retrieval:** dumping every applicable policy into prompts raises
  cost and lowers quality. Token-budgeted packs and per-call-site relevance
  filters are mandatory, not optional.
- **Extraction errors in obligations are high-stakes.** A missed "must
  have human review" is worse than a missed glossary term. Confidence plus
  mandatory human confirmation for anything that would *relax* a control.
- **Context freshness vs. reproducibility:** runs must pin versions
  (decided above), but should a *new* blueprint always use the latest
  context? Proposed: yes, with an explicit re-baseline action for existing
  ones.
- **Legal positioning:** the product must present this as decision
  *support* with citations, never as legal or compliance advice.
- **Starter regulatory packs need maintenance and ownership.** This is a
  content operation, not just engineering.

## Proposed epics

| # | Epic | Tier unlocked |
|---|---|---|
| 27 | **Organization Context Foundation**: org/BU/jurisdiction scope on processes; org profile wizard; policy-document ingestion into obligations/controls/roles/DoA with human confirm; starter regulatory packs | SMB |
| 28 | **Context Resolver & Context Packs**: applicability + filtered retrieval, versioned packs with citations, wired into gap analysis, blueprint, agent generation; staleness propagation | SMB+ |
| 29 | **Application & Data Catalog**: Application entity with aliases; system-name resolution; OpenAPI import (unblocks twin API mode); data classes driving permissions | Mid |
| 30 | **Compliance-Aware Testing & Release**: control coverage targets and DoA-derived reviewers in the test builder; control coverage in the Epic 23 release gate; compliance evidence view | Mid |
| 31 | **Enterprise Connectors & Federation**: job queue; MCP-based connectors (policy libraries, CMDB/EA, GRC, data catalog, IdP); federated stewardship and attribute-based access; per-BU model/provider policy and residency | Enterprise |

Suggested order: 27 → 28 (the minimum that changes outcomes, and it works
on uploaded documents alone), then 29 and 30 in parallel, then 31.
