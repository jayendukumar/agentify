"""Epic 38, US38.8: a live check of the operator console and support access.
Needs the stack with Keycloak (`.\\start.ps1 -Sso`): operators sign in
through realm "platform", Northwind's admin through realm "northwind".

    python testing/tenancy/smoke_operator.py

Creates a support grant in Northwind and revokes it again; changes no tenant.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "keycloak"))
from smoke_sso import PASSWORD, form_action, new_browser, otp_secret, totp  # noqa: E402

_getaddrinfo = socket.getaddrinfo


def _resolve_localhost(host, *args, **kwargs):
    if isinstance(host, str) and host.endswith(".localhost"):
        host = "127.0.0.1"
    return _getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _resolve_localhost

OPS = "http://ops.localhost:8000"
NORTHWIND = "http://northwind.localhost:8000"
results: list[bool] = []


def check(name: str, ok: bool, extra: object = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra != "" else ""))


def through_keycloak(login_url: str, username: str) -> tuple[httpx.Client, httpx.Response]:
    """Password, then the one-time code; returns where the API sent the browser."""
    browser = new_browser()
    to_idp = browser.get(login_url)
    page = browser.get(to_idp.headers["location"])
    response = browser.post(form_action(page.text), data={"username": username, "password": PASSWORD})
    if response.status_code == 200 and 'name="otp"' in response.text:
        response = browser.post(form_action(response.text), data={"otp": totp(otp_secret(username))})
    return browser, browser.get(response.headers["location"])


def main() -> int:
    check("the console's host serves the operator console",
          httpx.get(f"{OPS}/api/host").json()["kind"] == "operator")
    check("tenant routes don't exist on the console's host, nor the console's on a tenant's",
          httpx.get(f"{OPS}/api/processes").status_code == 404
          and httpx.get(f"{NORTHWIND}/api/operator/config").status_code == 404)

    otto, landed = through_keycloak(f"{OPS}/api/operator/oidc/login", "otto")
    check("Otto signs in through the platform realm with a one-time code",
          landed.headers.get("location") == "http://ops.localhost:3000/", landed.headers.get("location"))
    me = otto.get(f"{OPS}/api/operator/me").json()
    otto.headers["X-CSRF-Token"] = me["csrf_token"]
    _, refused = through_keycloak(f"{OPS}/api/operator/oidc/login", "petra")
    check("Petra, outside platform-operators, is refused",
          refused.headers.get("location", "").endswith("auth_error=not_an_operator"), refused.headers.get("location"))

    tenants = {t["slug"]: t for t in otto.get(f"{OPS}/api/operator/tenants").json()}
    check("Otto sees both tenants, with counts", {"meridian", "northwind"} <= set(tenants)
          and isinstance(tenants["meridian"]["usage"]["processes"], int), sorted(tenants))
    northwind = tenants["northwind"]
    check("with no grant, there's no way into Northwind",
          otto.get(f"{OPS}/api/operator/tenants/{northwind['id']}/support-grants").json() == [])
    check("the operator cookie means nothing on a tenant's host",
          otto.get(f"{NORTHWIND}/api/auth/me").status_code == 401)

    nora, _ = through_keycloak(f"{NORTHWIND}/api/auth/oidc/login", "nora")
    csrf = nora.get(f"{NORTHWIND}/api/auth/me").json()["csrf_token"]
    grant = nora.post(f"{NORTHWIND}/api/directory/support-grants", headers={"X-CSRF-Token": csrf},
                      json={"access": "read", "hours": 1, "reason": "smoke test: support access",
                            "operator_email": "otto@hosting.example"}).json()
    entry = otto.post(f"{OPS}/api/operator/tenants/{northwind['id']}/support-grants/{grant['id']}/enter")
    url = entry.json().get("url", "")
    check("Nora's grant lets Otto open a one-time link on Northwind's host",
          url.startswith(f"{NORTHWIND}/api/auth/support/enter?token="), url)

    support = new_browser()
    arrived = support.get(url)
    profile = support.get(f"{NORTHWIND}/api/auth/me").json()
    check("Otto lands in Northwind as support, read-only",
          arrived.headers.get("location") == "http://northwind.localhost:3000/"
          and profile.get("support", {}).get("access") == "read", profile.get("name"))
    support_csrf = profile.get("csrf_token", "")
    check("... can read", support.get(f"{NORTHWIND}/api/processes").status_code == 200)
    check("... and change nothing",
          support.post(f"{NORTHWIND}/api/processes", headers={"X-CSRF-Token": support_csrf},
                       json={"name": "support was here", "description": ""}).status_code == 403)
    check("the link works once", "auth_error" in new_browser().get(url).headers.get("location", ""))

    events = nora.get(f"{NORTHWIND}/api/audit-events", params={"action": "support_request"}).json()
    paths = {(e["detail"]["method"], e["detail"]["path"], e["outcome"]) for e in events
             if e["action"] == "support_request"}
    check("Northwind's audit log has every support request, with the operator",
          {("GET", "/api/processes", "success"), ("POST", "/api/processes", "refused")} <= paths, len(events))

    nora.post(f"{NORTHWIND}/api/directory/support-grants/{grant['id']}/revoke", headers={"X-CSRF-Token": csrf})
    check("Nora revokes it, and Otto's support session ends at the next request",
          support.get(f"{NORTHWIND}/api/processes").status_code == 401)

    audit = otto.get(f"{OPS}/api/operator/audit").json()
    check("the platform audit log has the sign-ins and the support session, not Northwind's content",
          {"operator_sign_in", "support_session_opened"} <= {e["action"] for e in audit}
          and "support was here" not in str(audit))

    # Sign out at Keycloak too: with two live sessions for one person,
    # Keycloak's admin sign-out sends a single back-channel token
    # (decision log, 2026-10-16), which would trip the next smoke test.
    for browser, api, path, token in ((nora, NORTHWIND, "/api/auth/logout", csrf),
                                      (otto, OPS, "/api/operator/logout", me["csrf_token"])):
        logout_url = browser.post(f"{api}{path}", headers={"X-CSRF-Token": token}).json().get("logout_url")
        if logout_url:
            browser.get(logout_url)

    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
