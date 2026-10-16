"""Epic 38, US38.12: a live check of creating a tenant and handing it to its
first admin. Needs the stack with local sign-in (`.\\start.ps1`): the
operator signs in by name on ops.localhost.

    python testing/tenancy/smoke_onboarding.py

Creates two tenants with names unique to this run, then suspends and
marks them for deletion (removing their data is US38.13):

- one by invitation: no local sign-in, the link signs the first admin in
  as Admin once, and is audited in both logs;
- one through an IdP: its sign-in page offers single sign-on at once.
"""

from __future__ import annotations

import sys
import time
from urllib.parse import urlsplit

import httpx

API = "http://127.0.0.1:8000"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def client(host: str) -> httpx.Client:
    # Python on Windows doesn't resolve *.localhost; the Host header picks the tenant.
    return httpx.Client(base_url=API, headers={"Host": f"{host}:8000"}, timeout=60)


def main() -> None:
    run = time.strftime("%H%M%S")
    ops = client("ops.localhost")
    response = ops.post("/api/operator/login", json={"name": "Smoke Operator", "email": "smoke@hosting.test"})
    if response.status_code != 200:
        sys.exit(f"Couldn't sign in to the console ({response.status_code}). Is the stack running with local sign-in?")
    ops.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    created_ids: list[str] = []
    try:
        # ---- by invitation ---------------------------------------------------------
        slug, host = f"smoke-inv-{run}", f"smoke-inv-{run}.localhost"
        neither = ops.post("/api/operator/tenants", json={"slug": slug, "host": host})
        check(neither.status_code == 400, "a tenant needs a way in for its first admin")
        created = ops.post("/api/operator/tenants", json={"slug": slug, "display_name": "Smoke Invite", "host": host,
                                                         "first_admin_email": "ada@smoke.test"})
        check(created.status_code == 201, f"created {slug} with an invitation ({created.status_code})")
        body = created.json()
        tenant_id = body["tenant"]["id"]
        created_ids.append(tenant_id)
        url = urlsplit(body["invitation_url"])
        check(url.hostname == host, f"the link is on the tenant's own host ({url.hostname})")

        browser = client(host)
        check(browser.get("/api/auth/config").json()["auth_mode"] == "none", "the new tenant offers no local sign-in")
        check(browser.post("/api/auth/login", json={"name": "Mallory", "roles": ["admin"]}).status_code == 404,
              "and the name picker is refused")
        landed = browser.get(f"{url.path}?{url.query}", follow_redirects=False)
        check(landed.status_code == 302 and landed.headers["location"].endswith("/directory"),
              f"the link signs the first admin in and lands on the Directory page ({landed.headers.get('location')})")
        me = browser.get("/api/auth/me").json()
        check(me.get("name") == "ada@smoke.test" and [g["role"] for g in me.get("roles", [])] == ["admin"],
              "as Admin")
        browser.headers["X-CSRF-Token"] = me["csrf_token"]
        local = browser.put("/api/directory/identity", json={"auth_mode": "local"})
        check(local.status_code == 400, "local sign-in can't be switched on for it")
        again = client(host).get(f"{url.path}?{url.query}", follow_redirects=False)
        check("invitation_refused" in again.headers.get("location", ""), "the link works once")
        reinvite = ops.post(f"/api/operator/tenants/{tenant_id}/invitation", json={"email": "otto@hosting.test"})
        check(reinvite.status_code == 409, "no new invitation once the tenant has an admin (no standing access)")

        events = browser.get("/api/audit-events").json()
        actions = {e["action"] for e in events}
        check({"tenant_created", "first_admin_invited", "first_admin_joined"} <= actions,
              "the tenant's own log has the creation, the invitation and the join")
        platform = {(e["action"], e["outcome"]) for e in ops.get("/api/operator/audit").json() if e["tenant_id"] == tenant_id}
        check({("tenant_created", "success"), ("first_admin_invited", "success"),
               ("first_admin_invited", "refused")} <= platform, "and so does the platform's")
        listed = {t["id"]: t for t in ops.get("/api/operator/tenants").json()}
        check(listed[tenant_id]["first_admin"]["state"] == "joined", "the console shows the first admin joined")

        # ---- through an IdP --------------------------------------------------------
        slug2, host2 = f"smoke-idp-{run}", f"smoke-idp-{run}.localhost"
        idp = ops.post("/api/operator/tenants", json={
            "slug": slug2, "display_name": "Smoke IdP", "host": host2,
            "idp": {"issuer": "http://localhost:8180/realms/northwind", "client_id": "axyntro",
                    "client_secret_ref": "secret://tenant/oidc_client_secret", "display_name": "Smoke SSO",
                    "acr_values": "mfa", "mfa_acr_values": ["mfa"], "admin_group": "smoke-admins"}})
        check(idp.status_code == 201 and idp.json()["invitation_url"] is None,
              f"created {slug2} with its IdP ({idp.status_code})")
        if idp.status_code == 201:
            created_ids.append(idp.json()["tenant"]["id"])
            config = client(host2).get("/api/auth/config").json()
            check((config["auth_mode"], config["sso_name"]) == ("oidc", "Smoke SSO"), "its sign-in page offers its SSO")
    finally:
        for tenant_id in created_ids:
            ops.post(f"/api/operator/tenants/{tenant_id}/suspend")
            ops.post(f"/api/operator/tenants/{tenant_id}/delete")

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
