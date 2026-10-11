# Epic 31 -- Enterprise Connectors & Federation

**Status: groomed, not started.** Groomed 2026-10-11 from
[enterprise-context-strategy.md](../enterprise-context-strategy.md).

Goal: let mid-size and large organizations keep context current
automatically, from the systems of record they already run, while each
business unit owns and sees its own slice and sensitive context stays
within the organization's model and data-residency policy.

Depends on: Epics 27-30. This epic changes *how context gets in and who
controls it*, not what the context model is.

## User Stories

US31.1 -- Background job queue.
As a Platform Engineer, I want a proper job queue (retries, schedules,
concurrency limits) replacing FastAPI `BackgroundTasks` for long-running
work -- connector syncs, re-embedding, stale propagation, and, eventually,
twin runs -- so ingestion at enterprise scale survives restarts and
doesn't compete with request handling.

US31.2 -- Pluggable connector framework.
As a Platform Engineer, I want connectors to be pluggable, with MCP as
the standard interface, so the product ships a few connectors and
customers' own teams can add more without forking the product. All
connectors are read-only: this product never becomes the system of record
for policies, applications, or identities.

US31.3 -- Policy library connectors.
As a Compliance Lead, I want policies synced from document libraries (e.g.
SharePoint, Confluence), with change detection, so a published policy
update creates a new context version and triggers Epic 28's staleness
propagation.

US31.4 -- Application portfolio connectors.
As an Automation Architect, I want applications synced from the IT service
management CMDB or the enterprise-architecture tool, merging into the Epic
29 catalog by alias.

US31.5 -- GRC and data-catalog connectors.
As a Compliance Lead, I want obligations, controls, and their mappings
synced from the GRC platform, and data classifications from the data
catalog, so the organization's existing compliance work is reused rather
than re-entered. Machine-readable control catalogs (e.g. NIST OSCAL) should
be importable directly.

US31.6 -- Identity connector.
As a Platform Engineer, I want roles and group membership from the
identity provider (e.g. Entra ID, Okta), so approval-authority roles map to
real people and product access can follow existing groups.

US31.7 -- Federated stewardship and scoped access.
As a business-unit Compliance Lead, I want to own (confirm, edit, retire)
my unit's context while group-wide policies apply to everyone, and users to
see only the context of the units they belong to (attribute-based access).

US31.8 -- Per-business-unit model and provider policy.
As a Compliance Lead, I want to choose which model provider each business
unit's context may be sent to (including a self-hosted option) and enforce
data residency, with personal data redacted before any LLM call. This
builds on Epic 20's `ModelPolicy`, and addresses the provider-hosting
concern recorded in the decision log (2026-09-17).

US31.9 -- Change impact analysis.
As a Process Owner, I want each incoming context change summarized as
impact ("AML policy v8 published -- 3 blueprints, 5 agents, 2 regression
suites affected"), with one action to review and re-run.

US31.10 -- Context access audit log.
As a Compliance Lead, I want an audit log of who and what (including which
LLM calls) read which context, so sensitive internal material is
traceable.

## Notes / Open Questions

- **Hard multi-tenancy** (unrelated organizations on one deployment) is
  explicitly *not* in this epic. Revisit it only if a hosted offering is
  pursued. It affects every table, and the local-first product doesn't
  need it.
- Postgres plus pgvector should hold into the low millions of passages. A
  dedicated graph database is only justified if applicability queries need
  deep multi-hop inheritance across legal entities. Measure before adopting.
- Ship connectors in order of observed demand. A connector nobody uses is
  pure maintenance cost.
