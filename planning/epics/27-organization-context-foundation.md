# Epic 27 -- Organization Context Foundation

**Status: groomed, not started.** Groomed 2026-10-11 from
[enterprise-context-strategy.md](../enterprise-context-strategy.md).

Goal: give the product a model of the *organization* a process belongs to
-- its business units, jurisdictions, roles and approval authority, and
the policies and regulatory obligations it operates under -- and a
lightweight way to fill that model for a small business with nothing more
than a short questionnaire and a few uploaded documents.

Depends on: Epic 1 (document ingestion -- reused for policy documents),
Epic 2 (knowledge store, pgvector), Epic 11 (confirm/dismiss UX pattern).
Feeds: Epic 28 (the resolver reads this store), Epics 29-31.

## Why first

Every later context feature needs two things this epic creates: an
organization to hang context on (today everything hangs off `process_id`),
and a *scope* on each process saying which part of the organization it
belongs to. Without scope, a Retail Banking EU process and an Insurance US
process with the same name would get the same context, which is wrong in
exactly the cases that matter.

## User Stories

US27.1 -- Organization, business units, jurisdictions.
As a Platform Engineer, I want `Organization`, `BusinessUnit` (vertical),
and `Jurisdiction` entities, with one organization per deployment for now,
so context can be scoped below the organization level. Hard multi-tenancy
(several unrelated organizations on one deployment) is out of scope -- see
Notes.

US27.2 -- Process context scope.
As a Process Analyst, I want to say once, when creating or editing a
process, which business unit it belongs to and which jurisdictions it
operates in, so everything downstream receives the right context. Existing
processes default to the organization-wide scope, which keeps them working
unchanged.

US27.3 -- Organization profile wizard.
As a small-business owner, I want a short guided questionnaire (industry,
countries, size, main applications, who approves what above which amount)
that creates my business units, jurisdictions, a starter application list,
and a basic approval-authority matrix, so the product has useful context
within a few minutes and without any integration work.

US27.4 -- Roles and approval authority.
As a Process Owner, I want named roles (e.g. "Head of Operations", "MLRO")
and an approval-authority matrix (role, decision type, monetary or risk
limit, scope), so escalation targets and reviewer thresholds can refer to
real roles instead of generic "a human".

US27.5 -- Policy, obligation, and control model.
As a Compliance Lead, I want `Regulation`, `Obligation`, `Control`, and
`Policy` entities, with each policy clause stored as retrievable text and
each obligation/control carrying `applies_to` scope (business unit,
jurisdiction, data class, process type), so "which rules apply here" can be
answered from structure rather than guessed.

US27.6 -- Policy-document ingestion with human confirmation.
As a Compliance Lead, I want to upload policy documents (handbook, expense
policy, AML policy) through the existing ingestion pipeline and have the
system propose the obligations, controls, roles, thresholds, and glossary
terms it finds, each with a source citation, so I confirm or correct them
rather than typing them in. Nothing extracted takes effect until confirmed.
Anything that would *relax* an existing control always needs explicit
confirmation (see Notes).

US27.7 -- Starter regulatory packs.
As a small-business owner, I want curated starter packs per industry and
geography (e.g. "EU -- general data protection basics", "US --
health-privacy basics") that add plain-language obligations and controls
for my selected jurisdictions, so I don't have to upload regulations
myself. Each pack is versioned and states clearly that it is decision
support, not legal advice.

US27.8 -- Glossary.
As a Process Analyst, I want an organization glossary (term, definition,
synonyms) populated from confirmed extractions or edited by hand, so
extraction and chat can resolve the organization's own vocabulary.

US27.9 -- Context browser.
As any persona, I want a read-only "Organization context" page showing
business units, roles, applications, and the rules that apply to a chosen
business unit and jurisdiction, so I can see what the system knows and
where it came from.

## Notes / Open Questions

- **Versioning:** every context entity is versioned in place (same
  convention as `TwinEnvironmentModel`: current state plus a version
  counter, with history rows). Epic 28 pins those versions.
- **Single organization per deployment** matches the product's
  local-first stance. Inter-organization isolation (tenancy) is revisited
  in Epic 31 only if a hosted offering is pursued.
- **High-stakes extraction:** a missed "must have human review" is worse
  than a missed glossary term. Extracted controls carry a confidence
  score. A low-confidence item, or one that would loosen an existing
  control, cannot be bulk-confirmed.
- **Starter packs are a content operation**, not just code: they need an
  owner, a review cadence, and a changelog. Ship two or three packs first
  rather than a broad, thin catalog.
- Policy clause text goes into pgvector with metadata (business unit,
  jurisdiction, effective dates, classification). This is the first data
  that Epic 28's retrieval will actually query.
- The current embedding model (384-dim MiniLM, Epic 2) is probably too weak
  for regulatory text. Evaluate a stronger local model behind
  `app/ingestion/embeddings.py` as part of this epic, and record the
  outcome in the decision log.
