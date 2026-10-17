# Local Keycloak: the stand-in customer IdP

Epic 37, step 3 (US37.7-37.8, and the IdP parts of US37.12-37.13). Run the
stack with sign-in through Keycloak instead of the name picker:

```powershell
.\start.ps1 -Sso            # or: docker compose -f docker-compose.yml -f docker-compose.sso.yml up -d --build
```

Then open http://localhost:3000 and choose **Sign in with Meridian SSO**.
Run `.\start.ps1` without `-Sso` to go back to local sign-in (this also
stops Keycloak).

Everything here is for local use only: the passwords, the client secret
and the authenticator secrets are in this repository.

## Who you can sign in as

Every password is `Axyntro!2026`. Each person also has an authenticator
already set up, so Keycloak asks for a one-time code after the password.

| Username | Groups | Roles in Axyntro |
|---|---|---|
| `sam` | axyntro-admins | Admin |
| `olivia` | axyntro-process-owners | Process owner |
| `morgan` | axyntro-auditors | Auditor |
| `arjun` | axyntro-architects, axyntro-analysts, axyntro-approvers | Architect, Analyst, Approver |
| `ana` | axyntro-analysts | Analyst |
| `avery` | axyntro-approvers | Approver |
| `nina` | axyntro-analysts | Analyst. Has no authenticator yet: Keycloak walks her through setting one up |

Epic 38: a second realm, `northwind`, is the identity provider of the
second tenant (http://northwind.localhost:3000). `start.ps1 -Sso` saves
Northwind's settings to use it; Meridian keeps realm `axyntro` through
the environment variables.

| Username | Groups (realm northwind) | Roles in Northwind | Authenticator secret (base32) |
|---|---|---|---|
| `nora` | northwind-admins | Admin | `MF4HS3TUOJXS2ZDFOYWW65DQFVXG64TB` |
| `nils` | northwind-analysts | None until an admin maps the group | `MF4HS3TUOJXS2ZDFOYWW65DQFVXGS3DT` |

The two realms don't know each other's people: Sam can't sign in to
Northwind, and Nora can't sign in to Meridian.

Epic 38, US38.8: a third realm, `platform`, is the hosting company's own
IdP for the operator console (http://ops.localhost:3000). Only members of
`platform-operators` get in.

| Username | Groups (realm platform) | In the console | Authenticator secret (base32) |
|---|---|---|---|
| `otto` | platform-operators | Operator | `MF4HS3TUOJXS2ZDFOYWW65DQFVXXI5DP` |
| `petra` | (none) | Refused: not an operator | `MF4HS3TUOJXS2ZDFOYWW65DQFVYGK5DS` |

Epic 39: a fourth realm, `core-banking`, isn't anyone's sign-in. It
stands in for a customer system's own authorization server. Its one
client, `agent-loans` (secret `agent-loans-dev-only-secret`), is a
service account a deployed agent gets tokens for by client credentials,
with the optional scopes `core.read` and `core.write`. The production
stand-in in `testing/runtime/` accepts only those tokens; see
`testing/runtime/smoke_runtime.py`.

To get a one-time code, add the person's secret to an authenticator app
(Google Authenticator, Microsoft Authenticator, 1Password). The secret is
`axyntro-dev-otp-<username>`, cut or zero-padded to exactly 20 characters and then
base32-encoded, because authenticator apps expect base32. Or print the
current code:

```powershell
python -c "import sys; sys.path.insert(0, 'testing/keycloak'); import smoke_sso as s; print(s.totp(s.otp_secret('sam')))"
```

Keycloak's admin console is http://localhost:8180 (`admin` / `admin`).

## What's configured, and why

The realm is `axyntro-realm.json`, imported when the container starts.

- **Client `axyntro`.** It's confidential, uses the authorization code
  flow only, and requires PKCE (S256). The redirect URI is the API's
  `/api/auth/oidc/callback`. Logout may return to the frontend. The
  back-channel logout URL is `http://api:8000/api/auth/oidc/backchannel-logout`,
  which Keycloak calls inside the compose network. The northwind realm
  uses the same URL: `api` reaches Meridian's host, and the API finds
  Northwind from the logout token's issuer (Epic 38, US38.7).
- **Fixed user IDs.** Each user has an `id` in the realm file, so
  re-importing a realm keeps their `sub` and the app still recognises
  them. To load a changed realm file, recreate the container
  (`docker compose -f docker-compose.yml -f docker-compose.sso.yml up -d --force-recreate keycloak`):
  Keycloak skips importing a realm that already exists.
- **Groups claim.** A mapper puts the user's group names (not full paths)
  into the ID token as `groups`. Until US37.11 brings admin-managed
  mappings, `OIDC_GROUP_ROLES` in `docker-compose.sso.yml` maps groups to
  roles.
- **MFA by level of authentication.** The browser flow `browser-mfa` has
  two levels. Level 1 is the password; level 2 adds the one-time code and
  is asked for on every sign-in. The client maps level 2 to `acr` `mfa`.
  Axyntro asks for it (`OIDC_ACR_VALUES=mfa`) and accepts only that as
  MFA (`AUTH_MFA_ACR_VALUES=["mfa"]`).
- **Issuer vs. internal address.** Browsers reach Keycloak at
  `localhost:8180`, and that's the issuer in every token. The API fetches
  discovery from `keycloak:8080`. `KC_HOSTNAME_BACKCHANNEL_DYNAMIC` makes
  the token and key endpoints it finds there use the internal address,
  while the issuer stays the same.

The realm deliberately does **not** set the client's *minimum ACR*. Without
it, a user who removes `acr_values` from the sign-in URL gets a
password-only token, and Axyntro refuses it. That refusal is what
`smoke_sso.py` checks. For a real deployment, set the minimum ACR in the
IdP as well: defence in depth.

## Provisioning over SCIM

Keycloak doesn't push users over SCIM, so locally you play the IdP's
provisioning side:

1. Sign in as `sam` (Admin), open **Directory** and generate a token.
2. Send requests to `http://localhost:8000/scim/v2` with
   `Authorization: Bearer <token>` and `Content-Type: application/scim+json`.

To link a provisioned person to their Keycloak sign-in, use their Keycloak
user id as the SCIM `externalId` (`SCIM_LINK_CLAIM=sub`). `smoke_scim.py`
does exactly that for `olivia`.

## The live smoke tests

```powershell
python testing/keycloak/smoke_sso.py    # sign-in, MFA, logout, sessions
python testing/keycloak/smoke_scim.py   # SCIM provisioning and group mappings
python testing/tenancy/smoke_tenant_sso.py   # Epic 38: Northwind through its own realm
python testing/tenancy/smoke_operator.py     # Epic 38: the operator console and support access
```

Run them a minute apart if they sign in the same person: Keycloak refuses
a one-time code that was already used.

`smoke_sso.py` signs in through Keycloak's real pages (a password, then a
computed one-time code) and checks:

- users are created from the ID token, with roles from their groups;
- the MFA refusal;
- Keycloak's back-channel logout ending an Axyntro session;
- a user disabled in Keycloak losing their session at the next refresh;
- logout through Keycloak;
- the audit trail.

`smoke_scim.py`:
- replays Entra ID's SCIM request sequence over HTTP;
- provisions `olivia` and checks she's linked at her first real sign-in,
  with roles from her SCIM group;
- previews a mapping change without applying it;
- deactivates her over SCIM and checks her session ends on the next
  request.
