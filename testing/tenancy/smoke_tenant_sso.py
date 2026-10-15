"""Epic 38, US38.6: a live check that each tenant signs in through its own
identity provider. Needs the stack with Keycloak (`.\\start.ps1 -Sso`),
which saves Northwind's settings (realm "northwind") while Meridian keeps
the environment's (realm "axyntro").

    python testing/tenancy/smoke_tenant_sso.py

Browsers resolve *.localhost by themselves; Python on Windows doesn't, so
this script points every *.localhost name at 127.0.0.1 itself.
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

KEYCLOAK = "http://localhost:8180"
NORTHWIND = "http://northwind.localhost:8000"
MERIDIAN = "http://localhost:8000"
results: list[bool] = []


def check(name: str, ok: bool, extra: object = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra != "" else ""))


def sign_in(api: str, username: str) -> tuple[httpx.Client, httpx.Response]:
    """Through the tenant's own Keycloak realm: password, then the code."""
    browser = new_browser()
    to_idp = browser.get(f"{api}/api/auth/oidc/login", params={"return_to": "/processes"})
    page = browser.get(to_idp.headers["location"])
    response = browser.post(form_action(page.text), data={"username": username, "password": PASSWORD})
    if response.status_code == 200 and 'name="otp"' in response.text:
        response = browser.post(form_action(response.text), data={"otp": totp(otp_secret(username))})
    if response.status_code != 302:
        return browser, response
    return browser, browser.get(response.headers["location"])


def main() -> int:
    northwind_config = httpx.get(f"{NORTHWIND}/api/auth/config").json()
    meridian_config = httpx.get(f"{MERIDIAN}/api/auth/config").json()
    check("each tenant offers its own sign-in button",
          (northwind_config["sso_name"], meridian_config["sso_name"]) == ("Northwind SSO", "Meridian SSO"),
          f"{northwind_config['sso_name']} / {meridian_config['sso_name']}")

    start = httpx.get(f"{NORTHWIND}/api/auth/oidc/login", follow_redirects=False).headers["location"]
    check("Northwind's sign-in goes to the northwind realm, and back to Northwind's host",
          start.startswith(f"{KEYCLOAK}/realms/northwind/")
          and "redirect_uri=http%3A%2F%2Fnorthwind.localhost%3A8000%2Fapi%2Fauth%2Foidc%2Fcallback" in start)

    nora, landed = sign_in(NORTHWIND, "nora")
    check("Nora signs in with password and one-time code, and lands on Northwind's frontend",
          landed.headers.get("location") == "http://northwind.localhost:3000/processes", landed.headers.get("location"))
    profile = nora.get(f"{NORTHWIND}/api/auth/me").json()
    check("she's a Northwind user, Admin through the northwind-admins group",
          profile.get("name") == "Nora Lund" and [(r["role"], r["source"]) for r in profile["roles"]] == [("admin", "group")],
          profile.get("roles"))

    _, refused = sign_in(NORTHWIND, "sam")
    check("a Meridian person can't sign in to Northwind (unknown in its realm)",
          refused.status_code == 200 and "Invalid username or password" in refused.text, refused.status_code)

    cookie = next(c.value for c in nora.cookies.jar if c.name == "asg_session")
    check("Nora's session is refused on Meridian's host",
          httpx.get(f"{MERIDIAN}/api/auth/me", cookies={"asg_session": cookie}).status_code == 401)

    csrf = profile["csrf_token"]
    settings = nora.get(f"{NORTHWIND}/api/directory/identity").json()
    check("Northwind's settings are its own, with URLs on its host",
          settings["source"] == "tenant" and settings["redirect_uri"] == f"{NORTHWIND}/api/auth/oidc/callback",
          settings["source"])
    editable = {k: settings[k] for k in (
        "auth_mode", "issuer", "discovery_url", "client_id", "client_secret_ref", "display_name", "scopes", "acr_values",
        "groups_claim", "jit_provisioning", "require_mfa", "mfa_amr_values", "mfa_acr_values", "scim_link_claim")}
    tested = nora.post(f"{NORTHWIND}/api/directory/identity/test", json=editable, headers={"X-CSRF-Token": csrf}).json()
    check("the connection test reaches the real realm", tested["ok"] and tested["signing_keys"] >= 1, tested.get("problems"))
    stolen = nora.put(f"{NORTHWIND}/api/directory/identity", headers={"X-CSRF-Token": csrf},
                      json={**editable, "client_secret_ref": "env:OIDC_CLIENT_SECRET"})
    check("Northwind's admin can't point at Meridian's client secret",
          stolen.status_code == 400 and "TENANT_NORTHWIND_" in stolen.json()["detail"], stolen.status_code)

    logout_url = nora.post(f"{NORTHWIND}/api/auth/logout", headers={"X-CSRF-Token": csrf}).json()["logout_url"]
    back = nora.get(logout_url)
    check("logout ends the session at the northwind realm and returns to Northwind",
          logout_url.startswith(f"{KEYCLOAK}/realms/northwind/")
          and back.headers.get("location", "").startswith("http://northwind.localhost:3000/"),
          back.headers.get("location"))

    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
