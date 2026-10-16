# Identity and Sign-in Manual Test Plan

Scope: Epic 37: local sign-in and its production guard (US37.14), roles
and segregation of duties (US37.1-37.6), sessions (US37.12-37.13), OIDC
single sign-on with MFA (US37.7-37.8), and SCIM provisioning with group
mappings (US37.10-37.11). SAML (US37.9) isn't built yet.

The app runs in **one sign-in mode at a time**. You choose it when you
start the stack:

| Mode | Start with | Sign-in page shows |
|---|---|---|
| Local (name picker) | `.\start.ps1` | A name box and role checkboxes, with a "Local sign-in -- not for production" banner |
| Single sign-on (Keycloak) | `.\start.ps1 -Sso` | One button: **Sign in with Meridian SSO** |

Check which mode is running at http://localhost:8000/healthz
(`"auth_mode": "local"` or `"oidc"`).

The frontend is http://localhost:3000, the API http://localhost:8000 and
Keycloak http://localhost:8180.

Every password, secret and token in this plan is for local use only and is
in the repository on purpose.

---

## Part 1 -- Local sign-in

### Setup

```powershell
.\start.ps1                              # empty development database
.\start.ps1 -Demo -ResetDemo -Seed       # or: the Meridian demo, built by the people below
```

There's no password. Typing a name you've used before signs in as that
same user, with the roles you tick this time. Ticking no role gives
read-only access.

### People

Type the name exactly as shown to act as someone from the seeded demo:

| Name to type | Roles to tick | Notes |
|---|---|---|
| `Sam Okafor (Admin)` | Admin | Runs the platform; can't build or approve |
| `Olivia Brandt (Process Owner)` | Process Owner | Approves process baselines |
| `Morgan Reyes (Auditor)` | Auditor | Reads everything, including the audit log |
| `Arjun Mehta (Automation Architect)` | Automation Architect, Process Analyst, Approver | Builds agents; may approve *other people's* |
| `Ana Costa (Process Analyst)` | Process Analyst | In the demo she's scoped to Retail Banking |
| `Avery Lindqvist (Approver)` | Approver | In the demo he's scoped to Retail Banking |

### Test cases

| ID | Steps | Expected |
|---|---|---|
| L-01 | Open the app and sign in with any name and no role | Read-only: pages load, actions are disabled, and any write returns 403 with a reason |
| L-02 | Sign in as `Arjun Mehta (Automation Architect)` with his three roles. Edit an agent's definition, then open its lifecycle panel | The Approve button is replaced by a message: you edited this version, so another approver must approve it |
| L-03 | Sign out, sign in as `Avery Lindqvist (Approver)`, open the same agent and approve it | Approved; the evidence export lists Avery as the approver |
| L-04 | As an analyst, finalize a process baseline | The version is "pending approval" and isn't used by the blueprint yet |
| L-05 | As `Olivia Brandt (Process Owner)`, approve that version | It becomes the current baseline |
| L-06 | As `Morgan Reyes (Auditor)`, open **Audit log**, filter by outcome "refused", export CSV | The refused approval from L-02 is listed; the CSV downloads |
| L-07 | Sign in under the same name in two browsers. In one, open **Sessions** and click **End session** on the other | The other browser goes to the sign-in page on its next click |
| L-08 | As `Sam Okafor (Admin)`, open **Users**, expand someone's sessions and click **Sign out everywhere** | All their browsers are signed out on their next click |
| L-09 | As Sam, deactivate a user | Their sessions end immediately, and they can't sign in again ("This account is deactivated") |
| L-10 | Leave the app idle for 30 minutes, then click something | You're sent back to the sign-in page (idle timeout) |
| L-11 | Stay active for 20 minutes | No interruption: the 15-minute session cookie is renewed silently in the background |
| L-12 | Start the API with `ENVIRONMENT=production` and `AUTH_MODE=local` | It exits at startup with an error naming both settings |

---

## Part 2 -- Single sign-on through Keycloak (OIDC + MFA)

### Setup

```powershell
.\start.ps1 -Sso
```

Then open http://localhost:3000 and click **Sign in with Meridian SSO**.
Keycloak's admin console is http://localhost:8180 (user `admin`,
password `admin`).

### People

Every password is **`Axyntro!2026`**. After the password, Keycloak asks
for a six-digit one-time code (MFA).

| Username | Keycloak groups | Roles in Axyntro | Authenticator secret (base32) |
|---|---|---|---|
| `sam` | axyntro-admins | Admin | `MF4HS3TUOJXS2ZDFOYWW65DQFVZWC3JQ` |
| `olivia` | axyntro-process-owners | Process Owner | `MF4HS3TUOJXS2ZDFOYWW65DQFVXWY2LW` |
| `morgan` | axyntro-auditors | Auditor | `MF4HS3TUOJXS2ZDFOYWW65DQFVWW64TH` |
| `arjun` | axyntro-architects, axyntro-analysts, axyntro-approvers | Architect, Analyst, Approver | `MF4HS3TUOJXS2ZDFOYWW65DQFVQXE2TV` |
| `ana` | axyntro-analysts | Analyst | `MF4HS3TUOJXS2ZDFOYWW65DQFVQW4YJQ` |
| `avery` | axyntro-approvers | Approver | `MF4HS3TUOJXS2ZDFOYWW65DQFVQXMZLS` |
| `nina` | axyntro-analysts | Analyst | None yet: Keycloak makes her set one up (scan the QR code) |

### Getting the one-time code

Either:
- add the secret to an authenticator app (Google Authenticator, Microsoft
  Authenticator, 1Password) as a time-based code, using "enter a setup
  key"; or
- print the current code from the repository root:

  ```powershell
  python -c "import sys; sys.path.insert(0, 'testing/keycloak'); import smoke_sso as s; print(s.totp(s.otp_secret('sam')))"
  ```

A code is accepted only once in its 30-second window. To sign in as the
same person twice in a row, wait for the next code.

### Test cases

| ID | Steps | Expected |
|---|---|---|
| S-01 | Click **Sign in with Meridian SSO**, then enter `sam`, the password and the code | Back in the app as Sam Okafor, with the Admin role; the Users page shows "Single sign-on" |
| S-02 | Sign in as `arjun` | Three roles, all from groups; on the Users page they're listed but can't be edited |
| S-03 | Start a sign-in. On the Keycloak page, delete `&acr_values=mfa` from the address bar and press Enter. Sign in with the password only | Keycloak doesn't ask for a code, but the app refuses: back on the sign-in page with "didn't use multi-factor authentication" |
| S-04 | After S-03, sign in as `morgan` and open **Audit log** | A refused `sign_in` with error `mfa_required` and the user's email |
| S-05 | Sign in as `nina` | Keycloak shows a QR code. Scan it, enter the code, and you're in as an Analyst |
| S-06 | Sign in as `sam`, then click **Log out**, then **Sign in with Meridian SSO** again | Keycloak asks for the password again: its session ended too |
| S-07 | Sign in as `arjun`. In the Keycloak console go to realm **axyntro** → Users → `arjun` → Sessions → **Sign out**. Click anything in the app | Arjun is sent to the sign-in page (back-channel logout) |
| S-08 | Sign in as `avery`. In Keycloak, turn off Users → `avery` → **Enabled**. Wait up to 15 minutes, using the app | Avery is signed out at the next session refresh. Turn Enabled back on afterwards |
| S-09 | In SSO mode, send `POST http://localhost:8000/api/auth/login` | 404: local sign-in is off |
| S-10 | Run `python testing/keycloak/smoke_sso.py` | `15/15 passed` |

---

## Part 3 -- SCIM provisioning and group mappings

SCIM needs SSO mode. Keycloak can't push users over SCIM, so in these
tests you play the identity provider's provisioning side by hand.

### Setup

1. Sign in as `sam`, open **Directory** and click **Generate token**. Copy
   the token: it's shown only once.
2. Note the SCIM base URL shown there: `http://localhost:8000/scim/v2`.
3. Send every SCIM request with these headers:
   - `Authorization: Bearer <token>`
   - `Content-Type: application/scim+json`

To link a provisioned person to their Keycloak sign-in, set their SCIM
`externalId` to their Keycloak user id. It's in the Keycloak console:
Users → the person → **ID**.

### Example requests (PowerShell)

```powershell
$h = @{ Authorization = "Bearer <token>"; "Content-Type" = "application/scim+json" }

# List provisioned users
Invoke-RestMethod http://localhost:8000/scim/v2/Users -Headers $h

# Deactivate a user (as Entra ID sends it)
$body = '{"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"Replace","path":"active","value":"False"}]}'
Invoke-RestMethod -Method Patch http://localhost:8000/scim/v2/Users/<id> -Headers $h -Body $body
```

### Test cases

| ID | Steps | Expected |
|---|---|---|
| P-01 | Send `GET /scim/v2/Users` with no token, then with a wrong one | 401, as a SCIM error |
| P-02 | `POST /scim/v2/Users` for `olivia@meridian.example`, with `externalId` set to Olivia's Keycloak id. Then `POST /scim/v2/Groups` named `axyntro-process-owners`, with her as a member | 201 for both. Users shows Olivia as "Provisioned (SCIM)" with group axyntro-process-owners |
| P-03 | Sign in as `olivia` through Keycloak | Same account as in P-02 (no duplicate), with the Process Owner role from the SCIM group |
| P-04 | Deactivate Olivia with the PATCH example above, then click something in her browser | Signed out on that click. Signing in again is refused ("deactivated"). The audit log shows `user_deactivated` by "SCIM (<token label>)" |
| P-05 | On **Directory → Group mappings**, add `axyntro-process-owners` → Auditor and click **Preview changes** | The preview lists Olivia gaining Auditor. Nothing changes until you save, and **Save** stays disabled until you've previewed |
| P-06 | Click **Save mappings**, then check Olivia on Users | She has Process Owner and Auditor, both from groups |
| P-07 | On Directory, click **Revoke** on the token and repeat a SCIM request | 401 |
| P-08 | Run `python testing/keycloak/smoke_scim.py` | `11/11 passed`. It generates its own token and leaves Olivia active at the end |

---

## Part 4 -- One identity provider per organization (Epic 38, US38.6)

### Setup

```powershell
.\start.ps1 -Sso
```

Two organizations run side by side: Meridian at http://localhost:3000 (or
http://meridian.localhost:3000), signing in through realm `axyntro`, and
Northwind at http://northwind.localhost:3000, through realm `northwind`.
Browsers open `*.localhost` addresses without any setup.

Northwind's people: `nora` (Admin) and `nils` (no role until mapped), both
with password `Axyntro!2026`. Their authenticator secrets are in
`testing/keycloak/README.md`.

### Test cases

| ID | Steps | Expected |
|---|---|---|
| T-01 | Open http://northwind.localhost:3000 | The button reads **Sign in with Northwind SSO**; on localhost:3000 it reads **Meridian SSO** |
| T-02 | Sign in to Northwind as `nora` | Keycloak's page says realm *Northwind*. After the code, you're back on northwind.localhost as Nora Lund, Admin |
| T-03 | On Northwind, try to sign in as `sam` | Keycloak: invalid username or password. Meridian's people don't exist there |
| T-04 | As Nora, open **Directory** | **Sign-in (identity provider)** shows "Saved here", and redirect URIs on northwind.localhost |
| T-05 | Change the client ID, then try to save | Save stays disabled until **Test connection** passes for exactly these values |
| T-06 | Set the client secret reference to `env:OIDC_CLIENT_SECRET` and test | Refused: Northwind's secrets must be named `TENANT_NORTHWIND_...` |
| T-07 | Click **Test connection** with the original values | "Connected: http://localhost:8180/realms/northwind, 1 signing key(s)" (or more) |
| T-08 | As Nora, sign out | Keycloak's northwind realm signs out and returns to northwind.localhost |
| T-09 | Sign in to Northwind as `nora` and to Meridian as `sam` (two browsers). In the Keycloak console, realm **northwind** → Users → `nora` → Sessions → **Sign out**. Click anything in both | Nora is sent to the sign-in page; Sam is still signed in (US38.7) |
| T-10 | Run `python testing/tenancy/smoke_tenant_sso.py` | `12/12 passed` |

If a save ever locks an organization out:
`docker compose exec api python -m app.tenancy identity reset northwind`.

## Part 5 -- The operator console and support access (Epic 38, US38.8)

### Setup

`.\start.ps1 -Sso`. The console is at http://ops.localhost:3000; operators
sign in through realm `platform` (`otto` is an operator, `petra` isn't).
Without `-Sso`, operators sign in by name (development only).

### Test cases

| ID | Steps | Expected |
|---|---|---|
| O-01 | Open http://ops.localhost:3000 and sign in as `otto` | The Tenants page: Meridian and Northwind with hosts, status and counts, and no process names or other content |
| O-02 | Sign in as `petra` | Refused: "Your account isn't in the platform operators' group" |
| O-03 | Open http://ops.localhost:8000/api/processes, and http://northwind.localhost:8000/api/operator/config | Both 404: tenant routes don't exist on the console's host, nor the console's on a tenant's |
| O-04 | Create tenant `acme` with host `acme.localhost`; open http://acme.localhost:3000 | The new tenant answers (no sign-in set up yet); the platform audit log shows `tenant_created` |
| O-05 | Suspend `acme`, then open http://acme.localhost:3000 | "This account is suspended"; Delete appears, and needs a second click to confirm |
| O-06 | In the console, open Northwind's **Support** | "No support access granted to you" |
| O-07 | As `nora` on Northwind: **Directory** → **Support access**, reason "Ticket 1", read-only, 2 hours, grant | Listed as active, for any operator |
| O-08 | In the console: Northwind's **Support** → **Open as support** | Northwind's app opens as "Otto Operator (support)", with the yellow support banner (read-only, until ...) |
| O-09 | As support, try to create a process | Refused: read-only support access |
| O-10 | As Nora, open **Audit log** | `support_session_started` and one `support_request` per request, with Otto's email |
| O-11 | As Nora, **Revoke** the grant, then click anything in Otto's window | Otto's support session ends (sign-in page) |
| O-12 | Run `python testing/tenancy/smoke_operator.py` | `15/15 passed` |

## Automated coverage behind this plan

- Backend: `tests/test_identity_and_sod.py`, `test_sessions.py`,
  `test_oidc.py` (a fake IdP with real signed tokens, including forged and
  tampered ones), `test_scim.py` (replays Entra ID's and Okta's documented
  SCIM sequences from `tests/fixtures/scim/`) and `test_directory_sso.py`.
- Frontend: `LoginPage.test.tsx`, `api/client.test.ts` (refresh and CSRF),
  `DirectoryPage.test.tsx`.
- Live: `testing/keycloak/smoke_sso.py` and `smoke_scim.py`.

More on the Keycloak setup is in `testing/keycloak/README.md`. The
concepts are explained in the wiki's Glossary page (`docs/wiki/Glossary.md`).
