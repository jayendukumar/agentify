# Glossary: identity, access and sessions

Last reviewed: 2026-10-15 · Sources: `backend/app/auth`, `backend/app/api/auth.py`, `backend/app/api/oidc.py`, `backend/app/api/deps.py`, `testing/keycloak`, `backend/app/config.py`, `frontend/src/api/client.ts`, `planning/epics/37-enterprise-identity-and-access.md`

These are the security and governance concepts behind Epic 37. Each entry
gives the general meaning and how Axyntro applies it, with the code that
does it. Concepts marked *(planned)* belong to steps not built yet (SAML,
SCIM). The reasons behind the choices are in the
[decision log](../../planning/decision-log.md), and the
[epic](../../planning/epics/37-enterprise-identity-and-access.md) has the
full stories.

## Who you are: authentication

| Term | What it means | In Axyntro |
|---|---|---|
| **Authentication** | Proving who someone is. | `AUTH_MODE=oidc`: the customer's IdP signs people in. `AUTH_MODE=local` (type a name, pick roles) remains for development and demos and is refused in production. |
| **Authorization** | Deciding what an authenticated person may do. | Roles grant permissions, which are checked on every write route against the target's scope (see below). Separate from authentication, so SSO only changes how people get in. |
| **Identity provider (IdP)** | The customer's directory that authenticates people: Entra ID, Okta, Ping, Keycloak, ADFS. | Any OIDC IdP. Locally, Keycloak runs in Docker Compose (`start.ps1 -Sso`, `testing/keycloak/`). |
| **Single sign-on (SSO)** | Signing in once with the company identity instead of a per-app password. | OIDC is built (US37.7). SAML is *(planned)* (US37.9). |
| **OpenID Connect (OIDC)** | An identity layer on OAuth 2.0. The IdP returns a signed **ID token** describing the user. | `app/auth/oidc.py`. Users are matched by issuer plus `sub` (the IdP's stable user id, in `user_identities`), never by email or name. |
| **Discovery document** | `/.well-known/openid-configuration`: the IdP's endpoints and where its keys are. | Cached for an hour. Its `issuer` must equal `OIDC_ISSUER` exactly. `OIDC_DISCOVERY_URL` lets the API fetch it from an internal address. |
| **Authorization Code flow with PKCE** | The OIDC flow where the browser receives only a short code, which the server exchanges for tokens. PKCE binds the exchange to the party that started it, `state` stops forged callbacks, and `nonce` stops a replayed ID token. | Run by the API (`/api/auth/oidc/login`, `/callback`), not the browser. State, nonce and verifier are held server-side for ten minutes and used once (`oidc_login_requests`). |
| **Login CSRF** | Tricking a browser into finishing a sign-in the attacker started, so the victim works in the attacker's account. | The state is also set in a short-lived cookie at `/login`, and the callback must present both. |
| **JWKS** | The IdP's published public keys, used to check ID token signatures. | Fetched from the discovery document and cached. An unknown key id triggers one refetch (key rotation), at most every 30 seconds. |
| **ID token validation** | The checks that make a token trustworthy. | Signature against the JWKS; an asymmetric algorithm only (never `none` or HMAC); issuer; audience (and `azp` when there are several); expiry and issue time, with 60 seconds of clock skew; the nonce. |
| **Claims** | The facts in a token: `sub`, `email`, `name`, `groups`, `amr`, `acr`, `sid`. | `sub` identifies, `groups` grant roles, `amr`/`acr` prove MFA, and `sid` links back-channel logout to sessions. |
| **SAML 2.0** | An older XML-based SSO standard, common with ADFS. The IdP sends a signed **assertion**. | *(planned)* US37.9: signed assertions required, replay protection on assertion ids. |
| **Multi-factor authentication (MFA)** | A second proof besides a password: an authenticator app, a hardware key. | US37.8. Enforced at the IdP, then *verified* here: the ID token must carry an accepted `amr` method or `acr` value, or the sign-in is refused and audited. The live test shows why: removing `acr_values` from the sign-in URL gets a password-only token from Keycloak, and the API refuses it. |
| **amr / acr** | `amr`: how the user authenticated (`pwd`, `otp`, `mfa`, `hwk`). `acr`: the assurance level reached. | `AUTH_MFA_AMR_VALUES` (default `mfa`, `otp`, `hwk`, `swk`) and `AUTH_MFA_ACR_VALUES`. The local Keycloak maps its level 2 (password + code) to `acr` "mfa". |
| **Level of authentication (LoA)** | Keycloak's step-up: flows gated by level, mapped to `acr` names. | The `browser-mfa` flow in the realm. Level 2 asks for the one-time code on every sign-in. |
| **SCIM** | A standard API the IdP uses to create, update and deactivate users and groups in an app. | *(planned)* US37.10 inbound. Unrelated to Epic 31's outbound `ScimConnector`, which reads groups into *context* roles. |
| **Just-in-time (JIT) provisioning** | Creating the user on first sign-in instead of in advance. | On by default (`OIDC_JIT_PROVISIONING`). Off: only people already known by issuer and subject may sign in. |
| **Group-to-role mapping** | Turning directory groups into product roles. | Until US37.11, `OIDC_GROUP_ROLES` maps groups to roles, optionally scoped `role@<business unit>`. It's reapplied at every sign-in; these grants are `source="group"`, read-only on the Users page. |
| **Groups overage** | Entra ID replaces the `groups` claim with a Graph link once a user is in too many groups. | Detected from `_claim_names`. The user keeps their previous group roles instead of losing them, and the sign-in audit event says so. |
| **RP-initiated logout** | The app sends the browser to the IdP to end the IdP session too. | Logout returns the IdP's `logout_url`, with the ID token as a hint. Without it, "Sign in" would go straight back in. |
| **Back-channel logout** | The IdP calls the app server to server when a user's IdP session ends. | `POST /api/auth/oidc/backchannel-logout` validates the signed logout token and revokes the sessions with that `sid` (or all of that `sub`'s). |
| **Backend-for-frontend (BFF)** | The server does the token exchange and keeps IdP tokens; the browser only holds the app's own session cookies. | The chosen design: no tokens in browser storage, and revocation is immediate because the server checks every session. |
| **Fail closed** | When configuration is missing or unsafe, refuse to run rather than run open. | US37.14: the API and job worker exit at startup if `AUTH_MODE=local` with `ENVIRONMENT=production`, if MFA is disabled in production, or if `AUTH_COOKIE_SECURE=false` in production. The Docker image defaults to `production`. `config.py` `_check_auth_safety`. |

## Staying signed in: sessions

| Term | What it means | In Axyntro |
|---|---|---|
| **Session** | One sign-in on one browser. | A `sessions` row with its creation time, last use, absolute expiry, revocation, IP and browser. Its id isn't secret; it's how the Sessions pages refer to it. `auth/sessions.py`. |
| **Access token** | A short-lived credential sent with every request. | The `asg_session` cookie, valid 15 minutes (`AUTH_ACCESS_TOKEN_MINUTES`). |
| **Refresh token** | A longer-lived credential used only to get a new access token. | The `asg_refresh` cookie, sent only to `/api/auth/refresh` because the cookie's path is scoped to it. |
| **Opaque token** | A random value that means nothing by itself; the server looks it up. The opposite is a self-contained token such as a JWT. | Both tokens are 32 random bytes. Being opaque is what makes instant revocation possible. |
| **Token hashing** | Storing only a hash of a token, so a database leak doesn't hand out working sessions. | `session_tokens.token_hash` is SHA-256. Before step 2 the cookie value *was* the primary key, in plain text. |
| **Token rotation** | Every refresh issues a new pair and retires the old one. | `sessions.refresh()`. Retired tokens are kept with `rotated_at` set, so their reuse can be recognized. |
| **Refresh-token reuse detection** | If a retired refresh token comes back, two parties hold it, so assume theft and end the session. | The session is revoked for both the thief and the real browser, and `refresh_token_reuse` is audited as refused. |
| **Grace window** | A short tolerance for a benign race. | Two tabs refreshing at once both present the same token. A reuse within 10 seconds gets 200 but **no new cookies**, so a thief gains nothing. `sessions.REUSE_GRACE`. |
| **Idle timeout** | The session ends after a period with no requests. | 30 minutes (`AUTH_IDLE_TIMEOUT_MINUTES`), measured from `last_used_at`, which is updated at most once a minute. |
| **Absolute lifetime** | A hard cap, however active the session is. | 12 hours (`AUTH_SESSION_MAX_HOURS`), so a session can't outlive a working day. |
| **Revocation** | Ending a session before it expires. | By signing out, by the user (the Sessions page, `SessionsPage.tsx`), by an admin (one session, or "Sign out everywhere"), by deactivating the user, or by the IdP (back-channel logout, or a refused IdP refresh). It takes effect on the next request. |
| **IdP-confirmed refresh** | Checking with the IdP that the account still exists before extending a session. | Each refresh of an SSO session redeems the IdP refresh token, stored encrypted with a key derived from the client secret. A refusal ends the session. An unreachable IdP doesn't: availability over strictness. |
| **Single-flight refresh** | When many requests fail at once, run one refresh and let the rest wait on it. | `refreshSession()` in `frontend/src/api/client.ts`, so a busy page doesn't trip reuse detection by itself. |
| **httpOnly cookie** | A cookie that page script can't read. | Both session cookies, so cross-site scripting can't steal them. |
| **Secure cookie** | A cookie sent only over HTTPS. | Defaults on in production only, because Python's and httpx's cookie jars won't send it over http even to localhost. `Settings.cookie_secure`. |
| **SameSite=Lax** | The browser doesn't attach the cookie to cross-site POSTs. | Both cookies. It's the first line of defence against CSRF. |
| **Same site vs same origin** | Origin is scheme + host + port; site ignores the port and subdomains. | `localhost:3000` (web) and `localhost:8000` (API) are different origins but the same site, so Lax cookies still flow between them. |
| **CORS with credentials** | Lets named other origins call the API with cookies and read the replies. | Only `localhost:3000` / `127.0.0.1:3000`. `X-Auth-Error` is exposed so the client can read it (`main.py`). |

## Forged requests: CSRF

| Term | What it means | In Axyntro |
|---|---|---|
| **CSRF (cross-site request forgery)** | Another site makes your browser send a request that carries your cookies. | Every write (anything but GET/HEAD/OPTIONS) needs the session's token in `X-CSRF-Token`, checked in `deps.get_current_session`. |
| **Synchronizer token** | A per-session secret the server stores and the client must echo in a header. | The pattern used. The token is returned in the bodies of sign-in, refresh and `/api/auth/me`, and the client keeps it in memory. |
| **Double-submit cookie** | The token is set in a readable cookie and echoed in a header. | Considered and rejected: script on :3000 can read a cookie from :8000 only because cookies ignore ports, which breaks if the API moves host. |
| **Stale CSRF token** | The tab holds a token for a session that was replaced, for example after signing in again in another tab. | The 403 carries `X-Auth-Error: csrf`; the client re-reads `/me` and retries once. |

## What you may do: roles and permissions

| Term | What it means | In Axyntro |
|---|---|---|
| **Role** | A named job function. | Analyst, Architect, Process owner, Approver, Auditor and Admin. A user can hold several. `auth/permissions.py`. |
| **Permission** | One action a role grants. | For example `process.edit`, `agent.approve`, `audit.read`. Routes ask for permissions, never role names, so the role matrix lives in one place. |
| **RBAC** | Role-based access control: permissions come from roles. | Role grants are rows in `user_roles`. |
| **Scope / scope chain** | Where a grant applies. A grant at a coarser level covers everything below it. | `organization` or `business_unit`. A check walks process -> business unit -> organization. A per-process scope can be added later without touching any guard. |
| **Route guard** | A dependency that refuses a request lacking the permission. | `require(permission)` in `api/deps.py`. A test walks every route and fails on an unguarded write. |
| **Least privilege** | Grant only what a job needs. | The Admin role runs the platform but can't build or approve: running the platform and approving what runs on it are separate duties. |
| **Read-only member** | Signed in with no roles. | Can read everything in their units and change nothing. |

## Approving change: segregation of duties

| Term | What it means | In Axyntro |
|---|---|---|
| **Segregation of duties (SoD)** | No one person both makes a change and approves it. | Evaluated on the object's history, not on roles: you may hold Architect *and* Approver, but you can't approve a change you made. `auth/sod.py`. |
| **Contribution** | A recorded change to a process or an agent, by someone, at a risk tier. | The `contributions` table, written in the same transaction as the change. |
| **Risk tier** | How much a kind of change matters for approval. | **High risk:** any change to the BPMN model or an agent definition. It needs a fresh, independent approval, with no minor-edit exemption. **Standard:** test assets, runs, release-candidate marking. These are recorded but never disqualify an approver. The tiers are data in `sod.py`. |
| **Change set** | Everything changed since the last approval. | What an approver is checked against. Each contribution gets a global `seq`. An agent's change set is everything after `approved_through_seq`. |
| **Four-eyes / two-person rule** | Require two distinct approvers. | US37.5: `required_agent_approvals`, set for the organization or per business unit. |
| **Pending baseline** | A finalized process version waiting for approval. | Finalizing now submits the version. Only the latest *approved* version feeds the blueprint, agents and staleness checks. |
| **Approval reason** | Explaining a refusal before it happens. | The approve button shows "You edited ... -- another approver must approve it" instead of failing on click. No role, including Admin, can override the rule. |

## Proving it: audit

| Term | What it means | In Axyntro |
|---|---|---|
| **Audit log** | An append-only record of who did what, when, and the outcome. | `audit_events`: sign-ins, sign-outs, session revocations, role changes, approvals, refusals, publishes. The API has no update or delete path. Shown on the Audit log page (`AuditLogPage.tsx`), with CSV export. |
| **Transactional audit** | The event exists exactly when the action committed. | Successful actions are written in the request's transaction. Refusals, whose request rolls back, are written in their own short transaction (`audit.record_refusal`). The one deliberate exception is refresh-token reuse, which commits the revocation together with its refused event. |
| **Actor type** | Whether a person, a service or the system acted. | `user` today. `service` is reserved for service accounts (backlog B4), so adding them needs no schema change. |
| **Evidence export** | The audit and approval trail packaged for compliance. | Epic 30's evidence export lists every approver and their roles. |
