"""Live smoke test of Epic 37 step 3 against the docker compose stack with
Keycloak (docker-compose.sso.yml). Signs in through Keycloak's real login
pages -- password, then a one-time code computed from the seeded
authenticator secret -- and checks MFA refusal, roles from groups,
logout through the IdP, back-channel logout and IdP-confirmed refresh.

    python testing/keycloak/smoke_sso.py
"""

from __future__ import annotations

import hashlib
import hmac
import html
import re
import struct
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx

API = "http://localhost:8000"
KEYCLOAK = "http://localhost:8180"
REALM = "axyntro"
PASSWORD = "Axyntro!2026"
results: list[bool] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""))


def totp(secret: str, at: float | None = None) -> str:
    """Keycloak keys TOTP with the secret's raw bytes (the QR code carries
    their base32 form)."""
    counter = int((at or time.time()) // 30)
    digest = hmac.new(secret.encode(), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


def otp_secret(username: str) -> str:
    return f"axyntro-dev-otp-{username}".ljust(20, "0")[:20]


def new_browser() -> httpx.Client:
    """Sends Keycloak's login cookies back the way a browser does. They're
    marked Secure, which browsers honour for http://localhost (a
    "potentially trustworthy" origin) but Python's cookie jar doesn't; and
    they carry `Version=1`, which browsers ignore but Python treats as an
    RFC 2965 cookie it won't return. Either way Keycloak answers "Cookie
    not found". (A cookie policy can't fix it: httpx copies the jar, with
    the default policy, for every request.)"""
    browser = httpx.Client(follow_redirects=False, timeout=20)

    def like_a_browser(response: httpx.Response) -> None:
        for cookie in browser.cookies.jar:
            cookie.secure, cookie.version = False, 0

    browser.event_hooks["response"].append(like_a_browser)
    return browser


def form_action(page: str) -> str:
    match = re.search(r'<form[^>]*action="([^"]+)"', page)
    if not match:
        raise RuntimeError(f"No form on the Keycloak page:\n{page[:500]}")
    return html.unescape(match.group(1))


def sign_in(username: str, *, with_otp: bool = True, strip_acr: bool = False) -> tuple[httpx.Client, str]:
    """Returns the browser and where our callback sent it."""
    browser = new_browser()
    to_idp = browser.get(f"{API}/api/auth/oidc/login", params={"return_to": "/processes"})
    authorize = to_idp.headers["location"]
    if strip_acr:  # what a user could do by editing the address bar
        parts = urlsplit(authorize)
        query = {k: v[0] for k, v in parse_qs(parts.query).items() if k != "acr_values"}
        authorize = urlunsplit(parts._replace(query=urlencode(query)))
    page = browser.get(authorize)
    response = browser.post(form_action(page.text), data={"username": username, "password": PASSWORD})
    if response.status_code == 200 and 'name="otp"' in response.text:
        if not with_otp:
            return browser, "stopped at the one-time code page"
        response = browser.post(form_action(response.text), data={"otp": totp(otp_secret(username))})
    if response.status_code != 302:
        raise RuntimeError(f"Keycloak didn't redirect back: {response.status_code} {response.text[:300]}")
    callback = response.headers["location"]
    landed = browser.get(callback)
    return browser, landed.headers.get("location", f"HTTP {landed.status_code}")


def me(browser: httpx.Client) -> httpx.Response:
    return browser.get(f"{API}/api/auth/me")


def admin_api() -> httpx.Client:
    token = httpx.post(
        f"{KEYCLOAK}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": "admin"},
    ).json()["access_token"]
    return httpx.Client(base_url=f"{KEYCLOAK}/admin/realms/{REALM}", headers={"Authorization": f"Bearer {token}"})


def user_id(admin: httpx.Client, username: str) -> str:
    return admin.get("/users", params={"username": username, "exact": "true"}).json()[0]["id"]


def main() -> int:
    # 1. Sign in with password + one-time code; roles come from groups.
    sam, landed = sign_in("sam")
    check("password + one-time code signs in and returns to the page asked for", landed == "http://localhost:3000/processes", landed)
    profile = me(sam).json()
    check("user created from the ID token", profile.get("name") == "Sam Okafor", profile.get("name"))
    check("roles come from Keycloak groups", [(g["role"], g["source"]) for g in profile["roles"]] == [("admin", "group")],
          str(profile["roles"]))
    check("the session is an SSO session", any(s["auth_method"] == "oidc" for s in sam.get(f"{API}/api/auth/sessions").json()))

    # 2. Arjun holds three groups.
    arjun, _ = sign_in("arjun")
    roles = sorted(g["role"] for g in me(arjun).json()["roles"])
    check("several groups give several roles", roles == ["analyst", "approver", "architect"], str(roles))

    # 3. MFA can't be skipped by editing the request to the IdP.
    _, landed = sign_in("ana", strip_acr=True)
    check("a sign-in without the one-time code is refused", "auth_error=mfa_required" in landed, landed)
    _, stopped = sign_in("ana", with_otp=False)
    check("Keycloak asks for the one-time code when MFA is requested", stopped == "stopped at the one-time code page")

    # 4. Back-channel logout: an admin ends Arjun's Keycloak sessions.
    admin = admin_api()
    before = me(arjun).status_code
    admin.post(f"/users/{user_id(admin, 'arjun')}/logout")
    time.sleep(1)
    check("Keycloak's back-channel logout ends the Axyntro session", before == 200 and me(arjun).status_code == 401)

    # 5. A user disabled in Keycloak loses their session at the next refresh.
    avery, _ = sign_in("avery")
    avery_id = user_id(admin, "avery")
    check("refresh works while the account is active", avery.post(f"{API}/api/auth/refresh").status_code == 200)
    admin.put(f"/users/{avery_id}", json={"enabled": False})
    try:
        refused = avery.post(f"{API}/api/auth/refresh").status_code
        check("refresh is refused once the account is disabled in Keycloak", refused == 401, str(refused))
    finally:
        admin.put(f"/users/{avery_id}", json={"enabled": True})

    # 6. Logout ends the Keycloak session too.
    csrf = me(sam).json()["csrf_token"]
    logout_url = sam.post(f"{API}/api/auth/logout", headers={"X-CSRF-Token": csrf}).json()["logout_url"]
    check("logout returns Keycloak's logout URL", bool(logout_url) and logout_url.startswith(f"{KEYCLOAK}/realms/{REALM}/"))
    back = sam.get(logout_url)
    check("Keycloak signs out and returns to the frontend",
          back.status_code == 302 and back.headers["location"].startswith("http://localhost:3000/"),
          f"{back.status_code} {back.headers.get('location')}")
    again = sam.get(f"{API}/api/auth/oidc/login").headers["location"]
    page = sam.get(again)
    check("signing in again asks for the password", 'name="password"' in page.text)

    # 7. The audit trail.
    morgan, _ = sign_in("morgan")
    events = morgan.get(f"{API}/api/audit-events", params={"limit": 100}).json()
    refused = [e for e in events if e["action"] == "sign_in" and e["outcome"] == "refused"]
    check("the MFA refusal is in the audit log",
          any(e["detail"].get("error") == "mfa_required" for e in refused), str([e["detail"].get("error") for e in refused]))
    check("the IdP logout is in the audit log", any(e["action"] == "idp_logout" for e in events))

    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
