# Epic 37 -- Enterprise Identity, Roles & Segregation of Duties

**Status: build steps 1-4 implemented. Step 1 (2026-10-14): US37.1-37.6,
US37.14, US37.15. Step 2 (2026-10-15): US37.12-37.13, sessions. Step 3
(2026-10-15): US37.7-37.8, OIDC sign-in and MFA, plus the IdP parts of
US37.12-37.13. Step 4 (2026-10-15): US37.10-37.11, SCIM provisioning and
group mappings. Open: US37.9 (SAML).** Groomed from the
enterprise-readiness ask: SSO with the customer's IdP, SCIM provisioning,
roles that match the work, segregation of duties, and short, revocable
sessions. Built in `app/auth/` (permissions, sod, audit, views),
`app/api/deps.py` (`require(permission)`), `app/api/governance.py` (users,
audit log, approval policy), baseline approval in `app/api/versions.py`,
migration `b7c8d9e0f1a2`. Frontend: role checkboxes on sign-in, a
local-mode banner, `ApprovalPanel` on Version history and each agent's
lifecycle panel, and the Audit log and Users pages.

Step 2 is built in `app/auth/sessions.py` (tokens, rotation, reuse
detection, revocation), `app/api/auth.py` (`/refresh`, own sessions),
`app/api/governance.py` (admin session endpoints) and migration
`c8d9e0f1a2b3`. Frontend: refresh-on-401 and the CSRF header in
`api/client.ts`, a Sessions page, and a sessions panel on Users.

Step 3 is built in `app/auth/oidc.py` (discovery, JWKS, token and logout
token validation, MFA, groups to roles) and `app/api/oidc.py` (`/login`,
`/callback`, `/backchannel-logout`), with migration `d9e0f1a2b3c4`
(`user_identities`, `oidc_login_requests`, IdP details on sessions).
Keycloak runs locally via `docker-compose.sso.yml` / `start.ps1 -Sso`.
The realm, its people and a live smoke test are in `testing/keycloak/`.

Step 4 is built in `app/scim/` (the protocol: filters and PATCH as Entra
ID and Okta send them; users and groups), `app/api/scim.py` (`/scim/v2`),
`app/auth/directory.py` (group mappings, preview, recomputing roles) and
`app/api/directory.py` (tokens and mappings for admins), with migration
`e0f1a2b3c4d5`. Frontend: a Directory page (SCIM tokens, the mapping
editor with its preview), and on Users, how each person was provisioned
and their groups. Request sequences for Entra ID and Okta are in
`backend/tests/fixtures/scim/`, and `testing/keycloak/smoke_scim.py` runs
the live check.

Deviations from the stories below, decided while building:
- US37.5's trigger is a per-organization / per-business-unit approval
  count, not "context pack carries high-risk obligations": obligations
  have no risk attribute yet.
- Role scopes drive what you may *change*; Epic 31's business-unit
  memberships still drive which context you *see*. They're separate
  tables, both managed by an admin.
- Publishing now requires an APPROVED agent (it didn't check before).
  The raw registry push (`POST /api/registries/{name}/push`) still takes
  any definition. It's guarded by `agent.publish` but not by approval.
- US37.12's CSRF token is returned in the body of sign-in, refresh and
  `/me` and sent back in `X-CSRF-Token`, rather than a double-submit
  cookie, so it still works if the API moves to another host. `/refresh`
  itself needs no CSRF header (see the decision log, 2026-10-15).
- A refresh token reused within 10 seconds of its rotation (two tabs
  refreshing at once) gets no new tokens but doesn't revoke the session.
  Later reuse does.
- `Secure` cookies default on only in production (`AUTH_COOKIE_SECURE`),
  because Python's and httpx's cookie jars won't send them over http,
  even to localhost.
- Step 3 built the IdP parts of US37.12/US37.13 too: each refresh of an
  SSO session redeems the IdP refresh token (stored encrypted), and the
  session ends if the IdP refuses it. If the IdP is unreachable, the
  session continues. OIDC back-channel logout revokes the matching
  sessions. SAML Single Logout waits for US37.9.
- Group-to-role mappings live in `group_role_mappings` (US37.11).
  `OIDC_GROUP_ROLES` only seeds the table, once, so a new deployment's
  first admin can sign in. Mappings are edited and saved as a whole set
  on the Directory page, after a preview; saving is refused until there's
  been a preview.
- US37.10's "tested against recorded Entra ID and Okta request
  sequences": the sequences are transcribed from each vendor's published
  SCIM documentation, not captured from a live tenant (there isn't one
  here). Each fixture names its source. Keycloak has no outbound SCIM, so
  the live check plays the IdP's provisioning side over HTTP.
- A SCIM user is linked to their first SSO sign-in by `SCIM_LINK_CLAIM`
  (default `sub`; `oid` for Entra ID), which must equal their SCIM
  `externalId`. That's an immutable IdP id, keeping the "never by email"
  rule. Someone who signed in with SSO before being provisioned is
  adopted when SCIM creates them with that externalId.
- For a SCIM-provisioned user, SCIM is the only source of groups; the
  sign-in groups claim is ignored. For everyone else, the groups claim at
  their last sign-in is kept (`users.idp_groups`), so a mapping change
  applies to them at once rather than at their next sign-in.
- MFA is verified from `acr` as well as `amr`. Keycloak signals it with
  a level of authentication mapped to `acr` "mfa" (`AUTH_MFA_ACR_VALUES`).
- `POST /api/auth/logout` returns 200 with the IdP's `logout_url`
  (RP-initiated logout) instead of 204.
- The frontend shows or hides actions by permissions held at *any*
  scope. A business-unit-scoped user sees enabled buttons on another
  unit's process and gets the server's 403 with a plain reason.

Goal: let an enterprise put this product behind its own identity
provider so that who can sign in, what they can do, and whether they may
approve something are all decided by the customer's directory and a
segregation-of-duties policy, not by a role someone picks on a login
screen.

Depends on: Epic 9/10 (US9.9/US10.4 session plumbing), Epic 23 (release
lifecycle and approval), Epic 31 (business-unit access, outbound SCIM
connector). Feeds: Epic 30 (compliance evidence now carries real,
separated approvers).

## Why

Today (`app/api/auth.py`, `app/api/deps.py`):

- **Anyone can be anyone.** "Log in" is a free-text name plus a role
  picked from a dropdown, with no password. Logging in again with another
  role silently changes that user's role for every existing session
  (`repository.get_or_create_user`).
- **Two roles: `viewer` and `editor`.** Every write route, ~250 of them,
  depends on `EditorDep` or `CurrentUserDep`, so building an agent,
  approving it, finalizing a baseline, publishing to a registry and
  confirming compliance context all need the same permission.
- **No segregation of duties.** `POST .../agent-artifacts/{id}/approve`
  only checks `EditorDep`. The person who generated and edited an agent
  can approve their own release, and the evidence export records that as
  an approval.
- **Sessions last 30 days and can't be managed.** The cookie value is the
  raw session id stored in the DB. There is no idle timeout, no rotation,
  and no way for an admin to see or end someone's sessions.
- **The existing SCIM code doesn't help here.** Epic 31's `ScimConnector`
  reads IdP groups into *context* roles (approval authority in the
  organization model). It doesn't create product users or control
  product access.

The US9.9 docstring on `UserModel` anticipated this: a real SSO login
should replace how users get into the table and the login endpoint, and
keep the `SessionModel` / `CurrentUserDep` plumbing. This epic does that
and also replaces the role check behind it.

## Design summary

- **Backend-for-frontend sessions, not tokens in the browser.** The API
  does the OIDC/SAML exchange server-side. The browser only ever holds
  two httponly cookies: a short-lived session cookie and a refresh
  cookie. IdP tokens stay on the server. This keeps the current cookie
  model, avoids storing tokens in browser storage, and makes revocation
  immediate because every session is checked against the DB.
- **Permissions, not role names, in route guards.** Roles map to a fixed
  set of permissions (`process.edit`, `agent.build`, `agent.approve`,
  `audit.read`, ...). Routes declare `require(Permission.AGENT_APPROVE)`,
  never `role == "approver"`. A user can hold several roles, typically
  one per IdP group.
- **Entitlements are scoped, coarse to fine.** A role grant carries a
  scope: organization-wide, a business unit, or (later) a single process.
  Business-unit scope ships now. The scope is stored as
  `(scope_type, scope_id)` and checks walk a process's scope chain
  (process -> business unit -> organization). Adding per-process
  ownership then means accepting one more `scope_type` and adding a UI
  for it, with no change to the guards or a data migration.
- **Segregation of duties is a policy evaluated on the object's history,
  not on the role.** Someone can hold both architect and approver roles.
  What's forbidden is approving *a change you contributed to*. That needs
  a record of who contributed to each version, which doesn't exist yet
  (`generated_by` only keeps the last regenerator).
- **Strictness follows risk.** Two kinds of change are *high-risk*:
  amending the business process (the BPMN model) and amending an agent
  definition. Any such change, however small, needs a fresh approval from
  someone who didn't contribute to it. Everything else (test
  environments, datasets, scenarios, runs, notes) is recorded for audit
  but doesn't block anyone from approving. The high-risk set is data in
  one policy module, so a customer can add to it.
- **One auth mode per deployment:** `AUTH_MODE=local | oidc | saml`.
  `local` (today's name picker) stays for development, tests and demos,
  and refuses to start when `ENVIRONMENT=production`.

## User Stories

### A. Roles, permissions and segregation of duties (no IdP needed)

Ships first: it changes outcomes even in `local` mode and is the part
every later story plugs into.

US37.1 -- Roles that match the work.
As an Admin, I want users to hold one or more of the roles analyst,
architect, process owner, approver, auditor and admin, each granting a
fixed set of permissions, so access follows what people actually do.
Acceptance: a `user_roles` table (user, role, `scope_type`, `scope_id`)
replaces `users.role`; `scope_type` is `organization` or `business_unit`
for now, and the column and the scope-chain check are built so `process`
can be added later (see Design summary); permissions are defined in one module
(`app/auth/permissions.py`) with the matrix below; a migration maps
existing `editor` users to analyst + architect + process owner and
`viewer` users to no role (read-only member); `UserOut` returns `roles`
and the effective `permissions`; the local login picker offers the new
roles (multi-select).

| Permission | Analyst | Architect | Process owner | Approver | Auditor | Admin |
|---|---|---|---|---|---|---|
| Read processes, blueprints, agents, results | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Upload documents, edit BPMN, chat edits, gap answers | ✓ | ✓ | ✓ | | | |
| Submit a process baseline for approval (finalize) | ✓ | | ✓ | | | |
| Approve a process baseline | | | ✓ | | | |
| Generate/edit agents, environments, datasets, test suites, run twins and batches | | ✓ | | | | |
| Mark release candidate | | ✓ | | | | |
| Approve an agent release / set release criteria | | | | ✓ | | |
| Publish to registry / mark deployed | | ✓ | | | | |
| Confirm organization context (policies, obligations) | | | ✓ | | | |
| Read audit log, export evidence | | | | ✓ | ✓ | ✓ |
| Manage IdP config, group mappings, users, sessions | | | | | | ✓ |

The admin role deliberately grants no build or approve permissions.
Running the platform and approving what runs on it are separate duties.

US37.2 -- Permission guards on every route.
As a Platform Engineer, I want every route to declare the permission it
needs, so no endpoint is accidentally open to any signed-in user.
Acceptance: `EditorDep` is replaced by `require(permission)` dependencies;
a test walks the FastAPI app's routes and fails if a non-GET route under
`/api` has no permission dependency (explicit allow-list for auth
endpoints); business-unit-scoped roles combine with
`app/context/access.py` (a process owner scoped to unit X can approve
only unit X's processes); a role is checked against the target's scope
chain, so a grant at a coarser scope covers everything beneath it; the frontend hides or disables actions based on
`permissions` from `/api/auth/me` instead of the ~16 `role === "editor"`
checks.

US37.3 -- Contribution history for processes and agents.
As an Approver, I want to see everyone who changed the process version or
agent version I'm approving, and how, so I know I'm not approving my own
work and can see whose work it is.
Acceptance: a `contributions` table records (object type, object id,
version, user, action, risk tier, at) for every change to a process or
an agent and its test assets. High-risk actions for a process: BPMN
edits, applied chat edits, document ingestion or regeneration that
changes the draft, and gap decisions that change the diagram. For an
agent: generate, regenerate and definition edit. Other actions
(environments, datasets, scenarios, runs, release-candidate marking) are
recorded as standard tier. The approval screen lists contributors for the
pending change, split by tier.

US37.4 -- Any high-risk change needs a fresh, independent approval.
As a Compliance Lead, I want every amendment to a business process or an
agent definition, including minor ones, to be approved by someone who
didn't make it, so nothing reaches production on one person's say-so.
Acceptance:
- **Agents:** any high-risk change to an approved agent clears its
  approval (`approved_at`/`approved_by`) and drops it back below
  APPROVED. Check whether regenerate does this today; if not, fix it and
  log the defect. There is no "minor edit" exemption.
- **Processes:** finalize no longer makes a baseline current directly.
  It creates a version in `pending_approval`, which a process owner
  in scope approves or rejects. Blueprint evaluation, agent generation
  and staleness keep using the latest *approved* baseline until then.
  This applies to every baseline, including the first. Editing the draft
  while a version is pending supersedes that version.
- **Who may approve:** `approve` (agent or baseline) returns 403 with a
  plain reason ("You edited the definition of version 4 on 12 Oct --
  another approver must approve it") when the approver made a high-risk
  change in the pending change set (since the last approved version).
  Standard-tier contributions don't disqualify an approver: an approver
  who added test scenarios can still approve.
- The approve button shows the reason instead of failing on click. No
  role, including admin, can override the rule.
- The rules and the high-risk action set live in `app/auth/sod.py` as
  data (object type, action, tier, forbidden-if-actor-did), so a customer
  can move an action into the high-risk tier with a one-line change.
- A publication requires an approval by someone other than the publisher.

US37.5 -- Optional two-person approval.
As a Compliance Lead in a regulated unit, I want to require two distinct
approvers for agents whose context pack carries high-risk obligations, so
the release gate matches our approval matrix.
Acceptance: an org/business-unit setting `required_approvals` (default
1); approvals become rows (`agent_approvals`) instead of a single
`approved_by` column, with the existing column kept as a view of the
first for compatibility; lifecycle reaches APPROVED only when the count
of distinct, SoD-eligible approvers meets the requirement; the Epic 30
evidence export lists all approvers and their roles.

US37.6 -- Audit log for identity and privileged actions.
As an Auditor, I want an append-only log of sign-ins, sign-outs, session
revocations, role and mapping changes, approvals, SoD refusals, publishes
and baseline finalizations, so I can show who did what and when.
Acceptance: an `audit_events` table (at, actor, action, object type/id,
outcome, ip, user agent, details) written in the same transaction as the
action; no update/delete path in the API; an Auditor page with filters
and CSV export; Epic 31's `context_access_log` stays as is and is linked
from the same page.

### B. Single sign-on

US37.7 -- OIDC sign-in with the customer's IdP.
As an Admin, I want users to sign in through our IdP (Entra ID, Okta,
Ping, Keycloak) over OpenID Connect, so we keep one identity, one
password policy and one offboarding process.
Acceptance: Authorization Code flow with PKCE, `state` and `nonce`,
executed by the API (`/api/auth/oidc/login`, `/api/auth/oidc/callback`);
discovery document and JWKS fetched and cached, ID token signature,
issuer, audience, expiry and nonce validated; the user is matched by
(issuer, `sub`), never by email or name; first sign-in creates the user
just in time if SCIM hasn't (configurable: `jit_provisioning=true|false`);
roles come from the `groups` claim through the group mappings in US37.11
when SCIM isn't used; client secret from an environment variable (same
`*_env` convention as connectors); the login page shows "Sign in with
<IdP name>" and no name picker.

US37.8 -- MFA enforced at the IdP, verified here.
As a Security Officer, I want the product to refuse sign-ins that didn't
use MFA, so a misconfigured IdP app can't quietly drop it.
Acceptance: the OIDC request asks for the configured `acr_values`; the
callback checks `amr` contains one of the configured methods (default
`mfa`, `otp`, `hwk`, `swk`) or `acr` matches; SAML checks
`AuthnContextClassRef`; on failure the sign-in is refused with a page
explaining that MFA is required, and an audit event is written; the check
can be switched off only through config (`require_mfa=false`), never in
the UI.

US37.9 -- SAML 2.0 sign-in.
As an Admin at an organization on ADFS or another SAML-only IdP, I want
SAML 2.0 SP-initiated SSO, so we can adopt the product without
standing up OIDC.
Acceptance: `python3-saml` (or `pysaml2`) behind the same login/session
code as OIDC; SP metadata endpoint; signed assertions required and
validated against the IdP certificate from metadata; NameID plus
configured attribute names for email, display name and groups; replay
protection on assertion IDs; IdP-initiated SSO is off by default.

### C. Provisioning

US37.10 -- SCIM 2.0 provisioning endpoint.
As an Admin, I want our IdP to push users and groups to the product over
SCIM, so joiners get access before their first day and leavers lose it
the moment they're disabled.
Acceptance: an inbound SCIM 2.0 server at `/scim/v2` (`Users`, `Groups`,
`ServiceProviderConfig`, `Schemas`, `ResourceTypes`) with filter support
for `userName eq` and `externalId eq` and PATCH as Entra ID and Okta send
it; authenticated by a bearer token the admin generates (stored hashed,
shown once, rotatable); `active=false` or DELETE deactivates the user and
revokes all their sessions in the same transaction; deactivated users
keep their history (contributions, approvals, audit) and can't sign in;
tested against recorded Entra ID and Okta request sequences.
This is separate from Epic 31's outbound `ScimConnector`, which keeps
feeding *context* roles. A note in the Admin UI says so, and one IdP
group can feed both.

US37.11 -- Groups mapped to roles.
As an Admin, I want to map IdP groups to product roles (optionally scoped
to a business unit), so access is managed in the directory, not in this
product.
Acceptance: a `group_role_mappings` table (group external id/display
name, role, `scope_type`, `scope_id`, matching `user_roles`); roles are recomputed when SCIM
changes membership or, without SCIM, at each sign-in from the `groups`
claim; roles granted by mappings can't be edited by hand in the UI
(only the mapping can); unmapped groups are ignored; a mapping preview
shows which users would gain or lose which role before saving; the Entra
ID "groups overage" case (claim replaced by a Graph link) is detected and
reported rather than treated as "no groups".

### D. Sessions

US37.12 -- Short sessions with rotating refresh tokens.
As a Security Officer, I want sessions that expire quickly when idle and
can't outlive a working day, so an unattended or stolen browser session
is of limited use.
Acceptance: two opaque, httponly, `Secure`, `SameSite=Lax` cookies: a
session cookie valid 15 minutes and a refresh cookie scoped to
`/api/auth/refresh`; tokens are random and stored only as SHA-256 hashes
(today the cookie value is the primary key); the frontend API client
refreshes on 401 once and retries; each refresh rotates the refresh
token, and reuse of an old one revokes the whole session family (theft
detection) and writes an audit event; idle timeout 30 minutes, absolute
lifetime 12 hours, all configurable; on refresh the user must still be
active, and in OIDC mode the IdP refresh token is used when present to
confirm the account still exists; state-changing requests require a CSRF
header (double-submit token) since cookies are now the only credential.

US37.13 -- Admin session revocation.
As an Admin, I want to see a user's active sessions and end one or all of
them, so I can respond to a lost laptop or an incident without waiting
for the IdP.
Acceptance: Admin > Users lists users with roles (and the groups they came
from), status and active sessions (created, last used, IP, user agent);
"Sign out everywhere" per user and "Revoke" per session take effect on
the next request (at most one request, because sessions are checked in
the DB); a user can see and end their own sessions; OIDC back-channel
logout and SAML Single Logout, when the IdP sends them, revoke the
matching sessions.

### E. Deployment safety and demo

US37.14 -- Local sign-in can't run in production.
As a Security Officer, I want the product to refuse to start in production
with the local name-picker sign-in, so a misconfigured deployment can't
let anyone in as anyone.
Acceptance:
- New settings `ENVIRONMENT` (`development` | `test` | `demo` |
  `production`, default `development`) and `AUTH_MODE` (`local` | `oidc`
  | `saml`, default `local`); unknown values are a startup error.
- Settings validation raises when `AUTH_MODE=local` and
  `ENVIRONMENT=production`, with a message naming both variables and
  what to set instead. The API and the standalone job worker
  (`python -m app.jobs.worker`) share this check, so both exit non-zero
  before binding a port or polling the queue. It isn't a logged warning
  that leaves the service up.
- The same check rejects `oidc`/`saml` with no IdP configuration, and
  `require_mfa=false` in production.
- The backend Dockerfile sets `ENVIRONMENT=production`, so a deployment
  that forgets to configure auth fails closed. `docker-compose.yml` and
  `start.ps1` set `development` (or `demo`) explicitly.
- In local mode, every page shows a "Local sign-in -- not for
  production" banner, and `/api/health` reports the auth mode and
  environment.
- Tests cover each refused combination, and the decision is logged in
  `planning/decision-log.md`.

US37.15 -- Demo seed with separated duties.
As a Presenter, I want the Meridian demo seeded with people in distinct
roles, so the demo shows segregation of duties working instead of one
all-powerful presenter.
Acceptance: `backend/scripts/demo_seed/seed_demo.py` and
`seed_process_twin.py` stop logging in as one `editor` and sign in as
named personas through the local login: an analyst (documents, BPMN), an
architect (agents, environments, twins), a process owner (approves
baselines) and an approver (approves agent releases), all scoped to the
Meridian business unit, plus an auditor. Each step is performed by the
persona whose role allows it, so every seeded baseline and agent is
approved by someone other than the person who changed it. The seed runs
with `ENVIRONMENT=demo`. The demo guide in `demo-deck/` gets a short "who
does what" section and one scripted moment where the architect's approve
button shows the SoD reason. Re-running the seed against an existing demo
database still works.

## Build order

1. **A (US37.1-37.6) with US37.14-37.15**: roles, guards, contributions,
   SoD, audit, the production guard on local mode, and the reseeded demo.
   Works in `local` mode, so it can be built and demoed without an IdP.
   It's also the largest change because it touches every route.
2. **US37.12-37.13**: session hardening and revocation. Small, and a
   prerequisite for exposing SSO.
3. **US37.7-37.8**: OIDC + MFA verification. Covers Entra ID, Okta, Ping
   and Keycloak.
4. **US37.10-37.11**: SCIM + group mappings.
5. **US37.9**: SAML, when a customer needs it.

## Testing

- Unit: signed ID tokens from a test JWKS (good, expired, wrong
  audience, wrong nonce, missing `amr`); SoD matrix tests per rule; the
  route-guard completeness test (US37.2); refresh rotation and reuse
  detection.
- Live, per the project's "run it against something real" rule: a
  Keycloak service in `docker-compose` (realm export checked in, with an
  OTP-required flow, groups, and a SCIM-capable setup or recorded SCIM
  traffic) used for a smoke test of sign-in, MFA refusal, group to role,
  deactivate to sessions revoked, and SoD refusal. Defects found there go
  to the decision log.
- Frontend: login page per mode, permission-driven action visibility,
  approve button showing the SoD reason, silent refresh on 401.

## Notes / Open Questions

- **Decided (2026-10-14): business-unit scope first, per-process scope
  kept open.** Roles are scoped to the organization or a business unit,
  reusing `app/context/access.py`. Some customers will want a named owner
  for one process inside a unit. The `(scope_type, scope_id)` grant and
  the scope-chain check (US37.1/37.2) let that be added as a new scope
  type with an admin UI, and IdP groups could map straight to it
  (`group_role_mappings` gets the same scope columns). Don't build it
  until a customer's owners don't line up with their units.
- **Decided (2026-10-14): strictness by risk tier.** Only amendments to
  the business process and to agent definitions are high-risk. Any such
  change needs independent approval, with no minor-edit exemption.
  Contributions to test assets are recorded but never disqualify. Worth
  revisiting: release-criteria changes (US35.7) can lower the bar an
  agent must clear. They're standard tier by default, but a regulated
  customer may want them high-risk, which is a one-line policy change.
- **Service accounts: backlog (B4).** No system accounts exist yet. The
  job worker runs in-process and doesn't call the API. Until then, the
  permission module and `contributions`/`audit_events` should record an
  actor *type* (`user` now, `service` later), so service accounts don't
  need a schema change. See [backlog.md](../backlog.md#b4--service-accounts).
