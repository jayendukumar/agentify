# Epic 29 -- Application & Data Catalog

**Status: groomed, not started.** Groomed 2026-10-11 from
[enterprise-context-strategy.md](../enterprise-context-strategy.md).

Goal: replace free-text system names (`tools_systems_needed:
list[str]`, used everywhere from extraction to the twin) with references to
a catalog of the organization's real applications -- each with aliases, an
owner, an API spec, and the classes of data it holds -- so agents and twin
tests are built against what actually exists.

Depends on: Epic 27 (organization, business units), Epic 28 (resolver).
Feeds: Epic 14/21 twin (real tool schemas, API mode), Epic 20 (permissions),
Epic 30.

## The gap this closes

The same application appears as "CRM", "CRM system", and "Salesforce"
across processes, and nothing links them.
`app/twin/schema_inference.py` invents an API for each name per agent.
Permissions are default-granted per string (`_build_default_permissions`).
Nothing knows which systems hold personal data or where they are hosted.

## User Stories

US29.1 -- Application entity with aliases.
As an Automation Architect, I want an `Application` entity (name, aliases,
owning business unit, owner role, hosting region, criticality, vendor), so
every mention of a system can resolve to one record.

US29.2 -- Bulk import.
As a Platform Engineer, I want to import applications from a CSV export
(the common denominator of CMDB, enterprise-architecture, and spreadsheet
inventories), with a preview and de-duplication against existing aliases.

US29.3 -- System-name resolution.
As a Process Analyst, I want system names found during extraction,
blueprint evaluation, and agent generation to be matched to catalog
applications automatically when unambiguous, and offered as a pick list
when ambiguous. Each place a system string is used gains an optional
`application_id`, and the string stays for backward compatibility.

US29.4 -- API spec import.
As an Automation Architect, I want to attach an OpenAPI document to an
application, so the twin uses the real operations and response shapes
instead of an LLM-invented schema. Inferred schemas remain the fallback for
applications without a spec.

US29.5 -- Twin API mode against a sandbox endpoint.
As an Automation Architect, I want an application with a spec and a
registered *non-production* endpoint to be callable in twin runs, which
finally delivers the "API mode" Epics 14, 20, and 21 all deferred. A
production-looking endpoint must be refused (see Notes).

US29.6 -- Data assets and classification.
As a Compliance Lead, I want data assets (Customer, Account, Payment,
Employee...) with a classification (Public / Internal / Confidential /
Confidential-PII / Restricted) and a residency, linked to the applications
that hold them, so sensitivity is known.

US29.7 -- Permissions from data classes.
As an Automation Architect, I want generated agents' permissions to default
from the data an application holds and the step's needs (e.g. read-only on
Confidential-PII unless the step explicitly writes it), replacing
default-grant-per-string.

US29.8 -- Third-party register view.
As a Compliance Lead, I want a view of which agents depend on which
applications and vendors, a by-product of the links above. Several
operational-resilience regulations require such a register.

## Notes / Open Questions

- **Production safety for API mode** is the riskiest story. It needs an
  explicit environment tag on every endpoint, an allow-list, and a hard
  refusal for anything tagged or resembling production. This needs a
  decision-log entry before it is built.
- Resolution confidence: auto-link only on exact alias match. Anything
  fuzzy goes to the user.
- Direct CMDB and EA-tool connectors belong in Epic 31. CSV import here
  covers the same data for small and mid-size organizations.
