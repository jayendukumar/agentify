# Epic 38 -- Multi-Tenancy: Many Organizations on One Deployment

**Status: build step 1 implemented (2026-10-15): US38.1-38.5, the tenant
boundary. Open: steps 2-5 (US38.6-38.15). Decisions confirmed 2026-10-15
(see Notes).** Reverses the earlier
"tenancy is a later concern" calls (Epic 27, Epic 31 notes,
`planning/enterprise-context-strategy.md` decision 1). Those were right
for a local-first product, and a hosted offering is now the goal. Weakness
2 in the wiki's Critical Review.

Goal: host several unrelated customers ("tenants") on one deployment, so
that no tenant can see, change or slow down another's data. Isolation is
enforced by the database, not only by the application, and each tenant
brings its own identity provider, policies and keys.

Depends on: Epic 37 (identity: every request already resolves to a user
and session; SCIM and group mappings exist and become per tenant), Epic
31 (job queue, connectors, model policy), Epic 27 (organization model:
each tenant has exactly one). Feeds: the enterprise-readiness plan, and a
hosted pilot.

## Why

Measured on 2026-10-15 (54 tables, 196 routes):

- **The organization is a singleton.** `get_organization_model()` takes
  the first `organizations` row and creates one if there's none. Seven
  call sites rely on that.
- **Nothing carries an owner above the process.** `processes` points to a
  business unit, not an organization. `users`, `jobs`, `context_entities`,
  `connectors`, `directory_groups`, `group_role_mappings` and `scim_tokens`
  are roots with no owner at all. About 30 tables hang off `process_id`;
  the rest hang off users, context entities or nothing.
- **Some uniqueness is deployment-wide** and would collide or leak between
  customers: `users.name`, `connectors.name`, and `context_packs.content_hash`
  (two tenants with identical context would share one pack row).
- **Data is read by id everywhere:** 162 `session.get(Model, id)` calls and
  123 `select(...)` statements. Ids are random, but one missed filter is a
  cross-tenant read, and a code review can't prove there are none.
- **Configuration is global.** The IdP (`OIDC_*`), registries, model
  provider, connector secrets (`token_env`) and the SCIM link claim are
  environment variables: one per deployment, not one per customer.
- **Work happens outside requests.** The job worker, document ingestion,
  context ingestion and the local registry open their own database
  sessions (`get_session_factory()`), with no request to say whose work it
  is. Jobs are claimed first-come, so one tenant's 10,000-case batch would
  starve everyone else.
- **Files and logs are shared.** Uploads go under one
  `DOCUMENT_STORAGE_PATH`; the LLM usage log and app log are single files
  with no tenant on each line.

## Design summary

- **Pooled by default: one database, one schema, a `tenant_id` on every
  tenant-owned row, enforced by Postgres row-level security (RLS).** Each
  transaction sets `app.tenant_id` (`SET LOCAL`). Every tenant table has
  `ENABLE` and `FORCE ROW LEVEL SECURITY` and a policy
  `tenant_id = current_setting('app.tenant_id')` for reads and writes. The
  column defaults to that same setting. So:
  - existing queries need no `WHERE tenant_id` and existing inserts no
    extra argument;
  - a forgotten filter can't leak, because the database refuses;
  - a transaction with no tenant set sees nothing and can insert nothing:
    it fails closed.

  The application connects as a role without `BYPASSRLS`. Migrations run
  as a separate owner role.
- **Siloed when a contract demands it.** A tenant record can name its own
  database. The same code and the same migrations run against it, and the
  connection is picked per tenant. This isn't built in the first steps;
  the design just doesn't rule it out. Schema-per-tenant was considered
  and rejected (see Notes).
- **A tenant is addressed by host name.** `acme.axyntro.app` maps to
  tenant `acme` through a `tenant_domains` table, and one tenant may have
  several hosts, including a custom domain. Cookies stay host-only, so a
  session can't be presented to another tenant's host, and a session row
  also records its tenant and is refused anywhere else. Locally,
  `meridian.localhost:3000` and `northwind.localhost:3000` work in
  browsers without any DNS setup.
- **Identity is per tenant.** Each tenant has its own IdP configuration
  (issuer, client id, a secret *reference*, MFA rules), SCIM tokens and
  group mappings: Epic 37's settings move from environment variables into
  the tenant record. A person in two tenants is two users, matched by
  (tenant, issuer, subject).
- **The platform operator is not a tenant admin.** A separate operator
  role creates, suspends and deletes tenants and sees health and usage,
  but has no access to tenant data. Support access is a time-limited
  grant the tenant's admin approves, recorded in that tenant's audit log.
- **Tenant context is explicit, never ambient.** Each request resolves its
  tenant from the host before authentication. Each job carries
  `tenant_id`, and the worker sets it before running the job. Each
  background session takes a tenant argument. Code that touches tenant
  data without a tenant fails (RLS), so the gaps surface in tests rather
  than in production.

## Step 1 as built

- **Code.** `app/tenancy/` (the tenant context, host resolution, the
  middleware, and `python -m app.tenancy` to manage tenants and hosts
  until the operator console exists), the `after_begin` hook in
  `app/db/session.py`, `TenantScoped` and the platform tables in
  `app/db/models.py`, and migration `f2a3b4c5d6e7`. The job worker walks
  the active tenants (`app/jobs/worker.py`).
- **Tests.** `tests/test_tenancy.py` (schema and grants, failing closed,
  hosts, per-tenant uniqueness, sessions, SCIM tokens, audit, jobs) and
  `tests/test_cross_tenant_probe.py` (US38.5). The suite connects as the
  app role and refuses to start as a superuser. Live:
  `testing/tenancy/smoke_tenancy.py`, plus Epic 37's smoke tests, which
  still pass under row-level security.
- **Local stack.** Existing data becomes tenant `meridian` (localhost and
  `meridian.localhost`); `start.ps1` adds `northwind`
  (`northwind.localhost`).

Deviations from the stories below, decided while building:

- **The tenant reaches the database through a ContextVar**, not an
  argument (the design summary said "explicit, never ambient"). It's set
  only at the edges (the middleware, the worker per job,
  `tenant_scope()`), and sessions read it in `after_begin`. Passing it
  explicitly would have meant changing every function that opens a
  session (the audit refusal writer, the local registry, four kinds of
  background task) for no gain in safety: a missing context still fails
  closed in the database.
- **The tenant column isn't in any composite index or foreign key.** Ids
  are random and globally unique, so `(tenant_id, id)` keys weren't
  needed for correctness. But Postgres checks foreign keys *without*
  row-level security, so a route that inserts a child without looking its
  parent up can link to another tenant's row. The probe found one
  (`PUT /processes/{id}/systems/{name}`); five routers now 404 an
  invisible process first (`api/deps.py` `ProcessScope`). Composite
  tenant foreign keys would close this in the database; they're a
  candidate for step 3.
- **US38.1, local tenants:** the second Keycloak realm (one per tenant)
  needs per-tenant IdP configuration, so it moves to step 2 (US38.6). On
  `-Sso`, single sign-on works for meridian only.
- **US38.3, caches:** the OIDC provider cache stays global until the IdP
  is per tenant (US38.6).
- **US38.4, hosts of an upgraded deployment:** the migration registers
  `TENANT_DEFAULT_HOSTS` plus the hosts of `FRONTEND_URL`,
  `API_PUBLIC_URL` and `OIDC_REDIRECT_URI`, so an existing deployment
  keeps answering where it did. Docker adds `api`, the host Keycloak uses
  for back-channel logout.
- **US38.5, what counts as a pass:** 404, or 422 when FastAPI rejects the
  query or body before any handler runs. Parameters that aren't a
  tenant's objects (node ids, system names, registry names, SCIM schema
  ids) aren't probed. The test asserts that over 80% of probes use real
  ids of the other tenant and over 60% reach a handler; on 2026-10-15 it
  sent 253 requests, 239 with real ids, 211 answered 404 by a handler.

## User Stories

### A. The tenant boundary

US38.1 -- Tenants and their hosts.
As a Platform Operator, I want each customer to be a tenant, reached at
its own host name, so customers never share a URL, a session or a sign-in
page.
Acceptance:
- **Platform tables** (no RLS, operator-only): `tenants` (id, slug,
  display name, status `active|suspended|deleting`, region, created) and
  `tenant_domains` (host -> tenant).
- **Resolving the tenant:** middleware maps the request's `Host` to a
  tenant before routing.
  - An unknown host gets a plain 404 page that names no tenant.
  - A suspended tenant gets a 403 page saying the account is suspended.
- **Frontend:** it calls the API on the same host, so the API base URL is
  derived per host rather than built in at compile time.
- **Local development:** the realm and seed data create two tenants,
  `meridian.localhost` and `northwind.localhost`.

US38.2 -- Every row belongs to a tenant, enforced by the database.
As a Security Officer, I want the database to refuse any read or write
outside the current tenant, so isolation doesn't depend on every query
being written correctly.
Acceptance:
- **Coverage:** every table except the platform tables gets
  `tenant_id NOT NULL`, defaulting to `current_setting('app.tenant_id')`,
  with RLS enabled and forced.
- **The policy:** reads, updates and deletes are limited to the current
  tenant, and inserts must carry it (`WITH CHECK`).
- **Database roles:** the API and worker connect as `axyntro_app`, which
  has no `BYPASSRLS` and doesn't own the tables. Alembic runs as
  `axyntro_owner`.
- **The schema test:** it walks the SQLAlchemy metadata and fails if a
  tenant table lacks the column, RLS or a policy, the same way the
  route-guard test walks routes (US37.2). The docs `check` reports it too.
- **Fail closed:** a test proves that a transaction with no tenant set
  reads no rows and can't insert.
- **Per-tenant uniqueness:** unique constraints become per tenant —
  `users(tenant_id, name)`, `connectors(tenant_id, name)`,
  `context_packs(tenant_id, content_hash)`, and identities
  `(tenant_id, issuer, subject)`.

US38.3 -- Tenant context everywhere work happens.
As a Platform Engineer, I want every request, job and background task to
run inside exactly one tenant, so nothing executes with "no tenant" or
the wrong one.
Acceptance:
- **Requests:** `get_db` runs `SET LOCAL app.tenant_id` from the resolved
  host.
- **Jobs:** the `jobs` table gets `tenant_id`, and the worker sets it for
  each job's transaction.
- **Background sessions:** `get_session_factory()` users (document
  ingestion, context ingestion, audit refusals, the local registry) take
  an explicit tenant and set it.
- **Sessions:** a session row records its tenant, and authentication
  refuses one presented on another tenant's host.
- **Caches:** the OIDC provider cache is keyed by tenant. Caches of static
  data (starter packs, the embedding model, settings) stay global.
- **Pooling:** this works with a transaction-mode connection pooler,
  because `SET LOCAL` lasts for one transaction only.

US38.4 -- Moving today's data into the first tenant.
As an Operator of an existing deployment, I want my current data to
become one tenant without loss, so upgrading isn't a reinstall.
Acceptance: a migration creates tenant `default` (its slug configurable)
and a domain for the current host. It backfills `tenant_id` on every row,
then makes it `NOT NULL` and turns RLS on, in that order and in one
transaction per table. The singleton `get_organization_model()` becomes
"this tenant's organization", one per tenant. A deployment that never
adds a second tenant behaves exactly as before.

US38.5 -- Proof that tenants can't reach each other.
As a Compliance Lead, I want an automated test that tries to cross the
boundary through every route, so isolation is demonstrated, not asserted.
Acceptance:
- **The setup:** a test builds two tenants with the same fixtures, signs
  in to each, and collects every id it created.
- **The probe:** it walks every route (FastAPI's route walk, as in US37.2),
  calling tenant B's routes with tenant A's ids in the path.
- **The pass condition:** every response is 404, never 200 or 403. A 403
  would confirm the object exists.
- **SCIM:** a SCIM token from tenant A gets 401 on tenant B's host.
- **Files:** a document's file can't be fetched from the other tenant.
- **Runs:** the test runs in the normal suite.

### B. Identity per tenant

US38.6 -- Each tenant brings its own IdP.
As a Tenant Admin, I want to configure our own OIDC provider, MFA rules
and sign-in name, so our people sign in with our directory and nobody
else's.
Acceptance:
- **What moves:** the `OIDC_*`, `AUTH_MFA_*`, `SCIM_LINK_CLAIM` and
  group-role settings move from the environment to a `tenant_identity`
  record. The client secret is held as a reference to the secret store
  (US38.10), never as a value.
- **Admin settings page:** an Admin edits the configuration there.
  - **Test connection:** fetches discovery and the keys, and checks that
    the issuer matches.
  - **Saving:** writes an audit event.
- **Redirects:** the redirect URI and the post-logout URI are derived from
  the tenant's host.
- **Environment variables:** they remain only as the defaults for the
  first tenant (US38.4).

US38.7 -- SCIM, mappings and sessions stay inside the tenant.
As a Tenant Admin, I want our SCIM token, groups and mappings to affect
only our tenant, so another customer's directory can never provision into
ours.
Acceptance: a SCIM token belongs to one tenant, and the tenant is taken
from the token, not the host. The IdP calls a fixed SCIM URL per tenant
host, and a token used on another tenant's host gets 401. Group mappings,
directory groups and users are tenant rows. Back-channel logout resolves
the tenant from the logout token's issuer.

US38.8 -- Operators without access to tenant data.
As a Customer, I want the hosting company's staff to be unable to read
our processes unless we let them, so hosting doesn't mean disclosure.
Acceptance:
- **Platform operators:** they sign in to a separate operator console
  through the platform's own IdP, with MFA required. They can list
  tenants, see health and usage, and create, suspend and delete tenants.
  Their sessions are platform sessions, and RLS gives them no tenant rows.
- **Support access:** a tenant admin grants it for a set time and scope
  (read-only by default). The grant is a row in the tenant, it expires by
  itself, and every action under it is audited in the tenant's log with
  the operator's identity.
- **No standing access:** no operator role can bypass RLS.

### C. Fair, separate resources

US38.9 -- Fair-share work and per-tenant limits.
As a Tenant Admin, I want our jobs to run even when another tenant
launches a huge batch, and to see what we've used, so we get predictable
service and cost.
Acceptance:
- **Fair claiming:** the worker claims jobs round-robin across tenants
  that have queued work, rather than oldest first. This is weakness 5's
  fair-share.
- **Limits per tenant:** concurrent jobs, LLM requests per minute, and a
  monthly LLM budget (warn at 80%, stop at 100%; batches already respect
  `stopped_budget`).
- **Usage:** LLM usage records carry `tenant_id` and go to a table, not
  only the JSONL file. An admin sees usage and cost per month.

US38.10 -- Per-tenant secrets and files.
As a Security Officer, I want each tenant's credentials and documents
kept apart, so one tenant's breach or deletion doesn't touch another's.
Acceptance:
- **Secrets:** IdP client secrets and connector tokens move to a secrets
  store, which is pluggable, with environment variables as the local
  backend. They're referenced as `secret://tenant/<name>`, so
  `token_env`-style environment names only remain for the first tenant.
- **Files:** uploaded files live under `tenants/<tenant_id>/`. When object
  storage lands (weakness 4), each tenant gets its own prefix and
  encryption key.
- **Logs:** the app log and the usage log include `tenant_id` on every
  line.

US38.11 -- Per-tenant model policy and registries.
As a Tenant Admin, I want our model routing, residency and agent
registries to be ours, so another tenant's choices never apply to our
data.
Acceptance: the organization-level `llm_policy` (`llm/routing.py`)
becomes the tenant's, inherited by business units as today. Registries
move from the `REGISTRIES` environment variable to tenant records. The
model provider's API key is either the platform's or a tenant's own (a
secret reference). Usage is attributed either way.

### D. Tenant lifecycle

US38.12 -- Create a tenant and its first admin.
As a Platform Operator, I want to create a tenant and hand it to its first
admin, so a new customer is live in minutes.
Acceptance: the operator console takes a slug, display name, host and
region, plus the first admin's email or an IdP setup.
- **No IdP yet:** a one-time invitation link (24 hours) lets the first
  admin set up SSO on the settings page, and no local name picker is ever
  enabled.
- **IdP supplied:** the tenant's group mappings are seeded so its admins
  group gets Admin.

Both are audited in the platform log and in the new tenant's log.

US38.13 -- Suspend, export and delete.
As a Customer, I want to take my data with me and have it deleted when we
leave, with evidence, so ending the contract is clean.
Acceptance:
- **Suspend:** sign-ins and SCIM are refused, jobs pause, and data is
  kept.
- **Export:** a job writes everything — every tenant table as JSON, the
  files, the audit log — to an archive. The admin downloads it once.
- **Delete:** a tenant can only be deleted after it's suspended and after
  a waiting period. Deletion removes every tenant row (one statement per
  table, under RLS as the tenant), its files and its secrets, and
  destroys its encryption key.
- **Evidence:** a deletion certificate (counts per table, time) is kept
  in the platform log. Nothing of the tenant's data remains in that log.

### E. Later, when a contract needs it

US38.14 -- A dedicated database for one tenant.
As a Customer in a regulated industry, I want our data in its own
database, so physical separation is part of our contract.
Acceptance: the tenant record can name a database URL (a secret
reference). The session factory picks the connection per tenant, and
migrations run against every tenant database. RLS stays on as defence in
depth. Moving a tenant in or out of a dedicated database is an export and
import (US38.13). Built only on demand.

US38.15 -- Data residency.
As a Customer in the EU, I want our data and model calls to stay in our
region, so we meet our data-protection obligations.
Acceptance: a tenant's region selects its deployment, with one deployment
per region rather than one database per region per tenant. Model policy
(US38.11) already restricts providers by region. Built when a second
region is needed.

## Build order

1. **US38.1-38.5:** the tenant boundary, the backfill and the isolation
   test. It touches every table, but because of the RLS defaults, few
   queries. Done when the cross-tenant probe passes for every route and
   the schema test is in CI.
2. **US38.6-38.8:** identity per tenant (moves Epic 37's settings into the
   tenant), and the operator console with support access.
3. **US38.9-38.11:** fair-share jobs, quotas and usage, secrets and files,
   model policy and registries per tenant.
4. **US38.12-38.13:** the tenant lifecycle. Needed before the first paying
   tenant beyond the pilot.
5. **US38.14-38.15:** only when a contract requires them.

## Testing

- **Schema (US38.2):** every tenant table has the column, RLS forced and a
  policy. A transaction without a tenant reads nothing and can't write.
- **Cross-tenant probe (US38.5):** every route, with the other tenant's
  ids, answers 404. SCIM tokens and sessions are refused on the other
  tenant's host.
- **Jobs and background work:** a job runs as its tenant; a job whose
  tenant is suspended waits; fair share keeps a small tenant's job within
  N seconds while another tenant has 10,000 queued.
- **Live, per the project's "run it against something real" rule:**
  - two tenants on `*.localhost`;
  - two Keycloak realms, one per tenant;
  - the Epic 37 smoke tests run once per tenant;
  - one more test signs in to `northwind` and tries `meridian`'s ids.

  Defects found there go to the decision log.
- **Performance:** RLS adds a filter to every query. Measure the hot paths
  (run steps, batch results) at Epic 34 volumes before and after, and
  index `tenant_id` first in the composite indexes that need it.

## Notes / Open Questions

- **Decided (confirmed 2026-10-15): pooled shared schema with RLS, with
  an opt-in dedicated database later.** Alternatives:
  - **Schema per tenant:** also needs almost no query changes, and
    isolation is good. But migrations run N times, cross-tenant
    operations (fair-share queues, usage, the operator console) get
    awkward, and Postgres degrades with thousands of schemas.
  - **Database per tenant from the start:** the strongest isolation and
    the most operations (connections, migrations, backups per tenant),
    and over-engineered before there are many tenants.

  Shared schema with RLS is the common default for B2B SaaS. The
  dedicated-database option (US38.14) covers the customers who need
  physical separation.
- **Decided (confirmed 2026-10-15): a host name per tenant**, not one
  host with the tenant chosen at sign-in. It keeps cookies, CORS
  and IdP redirect URIs naturally separate, and is how Entra ID and Okta
  app registrations expect it. The cost is wildcard DNS and TLS.
- **Decided (confirmed 2026-10-15): a person can't belong to several
  tenants under one login.** Same email in two tenants means two
  independent users, as with most B2B tools. Consultants serving several
  customers sign in to each separately.
- **Decided (confirmed 2026-10-15): platform analytics keep only usage
  counts and costs per tenant, never content.**
- **Out of scope:** billing and invoicing (usage records from US38.9 are
  the input), self-service sign-up, and moving tenants between regions.
- **Interactions:** Epic 30's evidence export and Epic 37's audit log
  become per tenant automatically through RLS. The starter regulatory
  packs (`app/context/starter_packs`) stay global, read-only content,
  copied into a tenant when applied, as today.
