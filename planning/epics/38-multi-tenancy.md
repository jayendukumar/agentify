# Epic 38 -- Multi-Tenancy: Many Organizations on One Deployment

**Status: build step 1 implemented (2026-10-15): US38.1-38.5, the tenant
boundary. Step 2 in progress: US38.6 (each tenant's own IdP) implemented
2026-10-15, US38.7 (SCIM and logout stay inside the tenant) and US38.8
(operator console, support access) 2026-10-16. Step 2 is complete.
Step 3 complete: US38.9 (fair share, limits, usage), US38.10 (secrets,
files, logs per tenant) and US38.11 (model policy, registries, provider
key per tenant) implemented 2026-10-16. Step 4 in progress: US38.12
(create a tenant and its first admin) implemented 2026-10-16; open:
US38.13-38.15. Decisions confirmed 2026-10-15
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
- **US38.1, local tenants:** the second Keycloak realm came with US38.6
  (`testing/keycloak/northwind-realm.json`).
- **US38.3, caches:** the OIDC provider cache became per tenant with
  US38.6.
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

## US38.6 as built

- **Code.** `tenant_identity` (one row per tenant, under row-level
  security; migration `a3b4c5d6e7f8`), `app/tenancy/identity.py`
  (resolving a tenant's configuration, validation, secret references),
  `app/auth/oidc.py` (providers per tenant configuration,
  `check_connection`), `GET/PUT /api/directory/identity` and
  `POST /api/directory/identity/test`, a "Sign-in" section on the
  Directory page, and `python -m app.tenancy identity show|set|reset`.
- **Tests.** `tests/test_tenant_identity.py` (a second tenant saves its
  IdP and signs in through it, with real signed tokens; refusals; the
  production rules). Live: `testing/tenancy/smoke_tenant_sso.py` (10/10)
  against two Keycloak realms, and Epic 37's smoke tests (15/15, 11/11)
  still pass for Meridian.

Deviations and decisions:

- **Environment variables are the first tenant's *fallback*, not a copy.**
  Without a saved row, the first tenant (`TENANT_DEFAULT_SLUG`) reads the
  `OIDC_*` variables at request time, so `start.ps1 -Sso` still switches
  it on and off. Its URLs stay as configured. Saving on the Directory
  page replaces them for that tenant. Any other tenant without a row has
  no single sign-on: local sign-in outside production, and nobody can
  sign in in production (the sign-in page says so) until an operator or
  US38.12's invitation sets it up.
- **The client secret reference is `env:TENANT_<SLUG>_...`** until the
  secrets store (US38.10). The prefix is enforced: an admin controls the
  IdP URL, and the secret is sent to its token endpoint, so a free choice
  of variable would let them collect the platform's model-provider key or
  another tenant's secret. The first tenant may also name the variable
  the operator configured (`OIDC_CLIENT_SECRET_ENV`).
- **In production, a tenant's IdP must be public https.** The connection
  test fetches it from inside the platform, so an internal address would
  be a way to probe the network (SSRF). Addresses that resolve to
  private, loopback or link-local ranges are refused.
- **Saving tests the connection again, server side,** and the page only
  enables Save after a passing test of exactly the values being saved: a
  bad save would lock everyone out at their next sign-in. If it happens
  anyway, `python -m app.tenancy identity reset <slug>` undoes it.
- **Back-channel logout** for a tenant other than the first came with
  US38.7, below.

## US38.7 as built

- **SCIM tokens** were already tenant rows since step 1: a token is looked
  up under the host's tenant, so on another tenant's host it isn't found
  (401, `tests/test_tenancy.py`), and the cross-tenant probe sends B's
  token at every SCIM route with A's ids. Groups, mappings and users are
  tenant rows. Nothing new was needed.
- **Back-channel logout** (`app/api/oidc.py`) no longer uses the host's
  tenant. The logout token's unverified `iss` and `aud` pick the tenants
  whose IdP is that issuer with that client; each verifies the signature
  with its own IdP's keys, and revokes and audits only its own sessions.
  A token no tenant trusts, or that fails every candidate's check, gets
  400 and ends nothing.
- **Tests.** `tests/test_tenant_identity.py`: a logout token for Northwind
  posted to the test tenant's host ends Nora's session there and is
  audited in Northwind only; another client, another issuer, a forged
  signature and garbage end nothing. Live: the northwind realm now calls
  `http://api:8000/...` (the first tenant's host) and
  `smoke_tenant_sso.py` (12/12) checks that a Keycloak admin's logout of
  Nora ends her Northwind session while Sam's Meridian session stays.

Deviations and decisions:

- **Candidates are found by walking the tenants**, one at a time under
  each one's row-level security, as the job worker does. A single lookup
  across tenants would need a role that bypasses row-level security, on
  an endpoint anyone can call. It's one small query per tenant per
  logout; a platform-level index of (issuer, client) is the fix if tenant
  counts make that matter.
- **Suspended tenants are included:** ending a session only protects them.
- **Two tenants may trust the same issuer and client.** Both check the
  token and both end the matching sessions: each has only sessions its
  own users started through that IdP, so neither can reach the other's.
- **Keycloak's local users have fixed IDs** in the realm files, so
  re-importing a realm keeps each person's `sub`; otherwise every
  re-import made new users at the next sign-in.

## US38.8 as built

- **Operator console** on its own host (`OPERATOR_HOSTS`, locally
  `ops.localhost`), which reaches no tenant: the middleware answers only
  `/api/operator/*` and `/api/host` there, and `/api/operator/*` answers
  on no tenant's host. `app/operators/`, `app/api/operator.py`, and a
  lazily loaded frontend (`frontend/src/operator/`) chosen by
  `GET /api/host`.
- **Operators** sign in through the platform's IdP (`OPERATOR_OIDC_*`;
  locally Keycloak realm `platform`) with MFA, and only members of
  `OPERATOR_GROUP`. Their sessions, sign-ins in flight and the platform
  audit log are platform tables (`OPERATOR_TABLES`).
- **A third database role**, `axyntro_platform` (migration
  `b4c5d6e7f8a9`), for the console: it may write `tenants` and
  `tenant_domains` and its own tables, has *no privilege on any tenant
  table*, and no BYPASSRLS. `tests/test_tenancy.py` checks every tenant
  table against it.
- **Tenants:** list (hosts, status, and usage as counts), create (slug,
  name, host, region), suspend, activate, delete.
- **Support access** (`app/auth/support.py`): a tenant admin grants it on
  the Directory page (`support_grants`, a tenant row): read-only by
  default, or "can edit"; for a number of hours (at most
  `SUPPORT_MAX_HOURS`, 72); optionally for one operator's email. The
  operator opens it from the console, which checks the grant *as the
  tenant* and writes a one-time entry (`support_entries`, a minute); the
  browser redeems it on the tenant's own host, which starts a tenant
  session for a support user (`actor_type` "support"). Every request in
  that session re-checks the grant, refuses writes under a read-only
  grant, and is written to the tenant's audit log with the operator's
  email.
- **Tests.** `tests/test_operator_console.py` (17), and the schema test
  for the platform role. Live: `testing/tenancy/smoke_operator.py`
  (15/15) against Keycloak realms `platform` and `northwind`.

Deviations and decisions:

- **Delete only marks the tenant `deleting`** (unreachable, data kept).
  Removing the data, with export, a waiting period and a certificate, is
  US38.13; the platform role deliberately can't delete `tenants` rows.
- **Usage counts are computed as each tenant**, by the app role under
  its row-level security, one short session per tenant: the platform
  role can't read tenant tables at all, and a cross-tenant view would
  need a role that can. Counts only: users, processes, waiting jobs,
  last sign-in, live support grants. Cost per tenant comes with usage
  records (US38.9).
- **Support roles come from the grant:** read-only gets Auditor, "can
  edit" gets Analyst, Architect, Process Owner and Auditor. Never Admin
  or Approver, so support can't widen its own access, change sign-in, or
  approve. The Users page refuses to change a support user's roles, and
  an admin who deactivates the support user keeps that operator out
  even under a live grant.
- **The console is closed by default** (`OPERATOR_AUTH_MODE=none`).
  Docker Compose sets `local` (a name, for development; refused in
  production) and `-Sso` sets `oidc`.
- **Creating a tenant doesn't yet hand it to a first admin**; that's
  US38.12 (since built: see below).

## US38.9 as built

- **Fair share** (`app/jobs/worker.py`): each tick, the worker hands free
  capacity out one job per tenant per round, to the tenants with due
  work, those with the fewest jobs running on this worker first (the
  rotation breaks ties). Within a tenant, priority and age order stay.
  A tenant drops out of the round once it has nothing due or is at its
  limit.
- **Limits** are columns on `tenants` (migration `c5d6e7f8a9b0`):
  `max_concurrent_jobs`, `llm_requests_per_minute`,
  `llm_monthly_budget_usd`. An operator sets them in the console
  (`PUT /api/operator/tenants/{id}/limits`, platform-audited); empty means
  the deployment's default (`TENANT_MAX_CONCURRENT_JOBS`,
  `TENANT_LLM_REQUESTS_PER_MINUTE`, `TENANT_LLM_MONTHLY_BUDGET_USD`), and
  no default means no limit. `app/tenancy/limits.py`.
  - **Concurrent jobs** are counted in the database (running jobs on every
    worker), under a per-tenant advisory lock while claiming.
  - **LLM requests per minute**: a call over the rate waits for the
    window rather than failing.
  - **Monthly budget**: before each call, the month's spend is read; at
    80% a `llm_budget_warning` goes to the tenant's audit log (once a
    month), at 100% calls are refused (`LLMBudgetError`, 429 on a
    request) and audited as `llm_budget_reached`. Batch and process-twin
    gates check it too, so a batch stops as `stopped_budget` rather than
    failing run by run.
- **Usage records** (`app/llm/metering.py`): every LLM call inside a
  tenant writes an `llm_usage_events` row and adds to
  `llm_usage_monthly` (month, operation, model), both tenant tables under
  row-level security. The JSONL log gains `tenant_id`.
- **Who sees it:** admins (`org.admin`) on a new Usage page
  (`GET /api/usage`): this month against the budget, the limits in
  effect, running and queued jobs, and cost per month and operation.
  Operators see each tenant's calls and cost this month, and its budget,
  in the console.
- **Tests.** `tests/test_tenant_limits.py` (13): a tenant with one job
  gets a slot on the first tick while another has 10,000 queued (whichever
  tenant the walk starts with); the less-served tenant gets the next free
  slot; two workers never exceed a tenant's concurrent limit; calls are
  recorded per tenant; warn at 80% once, refuse at 100% without reaching
  the provider; a batch stops on the tenant's budget; the rate waits;
  the admin and operator views. Live: `testing/tenancy/smoke_usage.py`
  (12/12), real extractions in Northwind.

Deviations and decisions:

- **Operators set the limits, tenants see them.** They're the hosting
  company's quota, on the platform table the platform role already
  writes and the app role can only read, so a tenant admin can't raise
  their own. A tenant-set budget *below* the quota is a later addition.
- **A monthly total next to the per-call rows.** The budget check runs
  before every LLM call; summing a month of calls each time would grow
  with usage, so `llm_usage_monthly` is kept up to date by an upsert and
  the check reads a handful of rows.
- **Requests per minute are counted per process.** A window shared
  across processes needs a shared counter (a table row or Redis) on
  every call; with the worker in the API process (the default), one
  process is the whole deployment. With a separate worker, a tenant can
  make its rate in each.
- **Limits are read at most every 30 seconds** per process, so an
  operator's change takes up to that long to apply. The spend itself is
  read fresh before every call.
- **Unpriced models cost nothing against the budget.** Calls to a model
  without a known price (`app/llm/usage.py`) are counted as
  `calls_without_cost` and shown as such, rather than guessed.
- **Fair share is per tenant, not per process** within a tenant: a
  tenant's own big batch can still hold back its other work.

## US38.10 as built

- **Secrets store** (`app/secret_store`): stored settings hold a
  reference, `secret://tenant/<name>`, which only ever reaches the
  current tenant's secrets. Pluggable with `SECRETS_BACKEND`: `env`
  (default) reads `TENANT_<SLUG>_<NAME>`, `files` reads a file of that
  name in `SECRETS_DIR` (Docker or Kubernetes secrets). References are
  checked when saved and again when used.
- **Where references are used:** the IdP client secret
  (`tenant_identity.client_secret_ref`), connector tokens (`token_ref`,
  new), and a model endpoint's key in the LLM policy (`api_key_env` now
  takes a reference too). Environment names (`env:NAME`, `token_env`, a
  bare `api_key_env`) remain for the first tenant only; the operator
  console's own IdP keeps its environment variable.
- **Migration `d6e7f8a9b0c1`** turns a tenant's own
  `env:TENANT_<SLUG>_<NAME>` into `secret://tenant/<name>` (the env
  backend reads the same variable, so nothing is set again) and moves
  uploads into `tenants/<tenant_id>/`, as each tenant.
- **Files** (`app/tenancy/files.py`): `<DOCUMENT_STORAGE_PATH>/tenants/
  <tenant_id>/<process_id>/` and `.../_organization/`. A file name is cut
  to its last component and the path must stay inside the tenant's
  directory; without a tenant, nothing is stored or read. Deleting a
  process now deletes its files.
- **Logs:** every app-log line carries `[tenant=<id>]` (or `-` outside a
  tenant); the usage JSONL has `tenant_id` since US38.9. A standalone
  worker (`python -m app.jobs.worker`) now configures logging the same
  way; before, it logged nothing below warning.
- **UI:** the Directory page suggests `secret://tenant/oidc_client_secret`
  and shows where the hosting company stores it; the Connectors panel's
  examples use `token_ref`.
- **Tests.** `tests/test_tenant_secrets_files.py` (12). Live:
  `testing/tenancy/smoke_secrets_files.py` (13/13).

Deviations and decisions:

- **Environment variables and mounted files, not a cloud secrets
  manager.** The store is an interface with two backends; Vault or a
  cloud manager is one more class when a deployment needs it.
- **Model endpoint keys were included** although US38.11 owns model
  policy: the same hole (a tenant naming the platform's key and sending
  it to an endpoint it chose) existed there, so it's closed now. Choosing
  between the platform's key and a tenant's own for the default provider
  stays with US38.11.
- **Old environment names in other tenants stop resolving** rather than
  being migrated when they aren't the tenant's own `TENANT_<SLUG>_*`
  variable: the connector or sign-in says which secret to set.
- **Per-tenant encryption keys** wait for object storage (weakness 4).

## US38.11 as built

- **Model policy:** already the tenant's. `organizations` is a tenant
  table since step 1, so the organization-wide `llm_policy` and each
  business unit's are the tenant's own, inherited as before. A test now
  shows one tenant's policy never applies to another's calls.
- **Registries** are tenant records (`registries`, migration
  `f9a0b1c2d3e4`), set by admins on the Registries page
  (`GET/PUT /api/organization/registries`, audited). Without rows, the
  first tenant keeps `REGISTRIES` and any other tenant gets the built-in
  local registry, whose entries are tenant rows anyway. A registry's
  config holds only secret references (`*_ref`).
- **Provider key:** an admin can point the default provider at the
  organization's own key (`PUT /api/organization/llm-provider-key`, a
  `secret://tenant/<name>` reference, on the Organization page's
  governance panel). Set but missing, calls are refused rather than put
  on the platform's key. Clients are cached per key value, so a rotated
  key gets a new one.
- **Attribution:** usage rows carry `key_source` (`platform` or
  `tenant`; a self-hosted endpoint counts as the tenant's). Usage is the
  tenant's either way. The monthly budget, the batch budget gate and the
  operator console's cost count the platform's key only; the Usage page
  shows what was spent on the tenant's own key.
- **Tests.** `tests/test_tenant_model_policy.py` (10). Live:
  `testing/tenancy/smoke_model_policy.py` (12/12), including a real call
  on Northwind's own key from inside the API container.

Deviations and decisions:

- **The budget is the platform's quota**, so calls on a tenant's own key
  aren't held to it (requests per minute still apply). A tenant wanting a
  cap on its own key's spend is a later option.
- **Registries replace as a list** (PUT), and an empty list returns to
  the default. Entries pushed to a removed registry stay, unsearched.
- **No registry types beyond `local`** yet; config and secret references
  are in place for the first external one.

## US38.12 as built

- **The console's Create a tenant** takes the slug, name, host and region,
  and exactly one way in for the first admin (`TenantCreate`
  `first_admin_email` or `idp`). An IdP setup is validated before
  anything is created.
- **By invitation** (`app/tenancy/onboarding.py`): a one-time link on the
  tenant's own host (`/api/auth/invitation/accept`), valid 24 hours,
  shown once in the console and stored as a hash (`tenant_invitations`).
  It signs the first admin in as Admin and lands them on the Directory
  page to set up single sign-on. "New invitation" replaces an unused
  link.
- **No local sign-in, ever,** for console-created tenants
  (`tenants.created_via = 'console'`, migration `a0b1c2d3e4f5`): until
  SSO is set up their sign-in mode is `none`, in development too, and
  the Directory page refuses to save local sign-in.
- **Through an IdP:** the tenant's identity settings are saved (not
  tested against the IdP: it may not trust the new host yet; the
  Directory page's test does that) and its admins group is mapped to
  Admin, as `python -m app.tenancy identity set --admin-group` does.
- **Audit:** the platform log has `tenant_created` and
  `first_admin_invited` or `tenant_idp_configured`; the tenant's log has
  `tenant_created`, `first_admin_invited` or `identity_settings_changed`
  (by the operator), and `first_admin_joined` when the link is used.
- **The console** shows each tenant's first admin (invited, expired,
  joined, through its IdP) and offers a new invitation while nobody has
  joined.
- **Tests.** `tests/test_tenant_onboarding.py` (11). Live:
  `testing/tenancy/smoke_onboarding.py` (15/15).

Deviations and decisions:

- **No standing access through invitations.** A link can only be made,
  and only works, while the tenant has neither single sign-on nor an
  active admin; otherwise an operator could invite themselves into any
  tenant. Once the first admin has joined, the tenant's own admins add
  people.
- **The operator sends the link;** the product doesn't send email yet.
  The operator could use the link themselves before the admin does:
  the join is audited with its IP in the tenant's log, and the admin
  would find the link already used.
- **The link is a GET with the token in the query**, so it works from an
  email; it may appear in access logs until it's used or expires.
- **Not tested live with a real IdP sign-in** (Keycloak wasn't running):
  the IdP path is checked up to the tenant's sign-in page offering its
  SSO; the sign-in itself is US38.6's, tested there.
- **If the tenant is created but its first admin step fails**, the tenant
  stays (its row is committed first, because the tenant's own rows
  reference it) and the console offers a new invitation.

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
