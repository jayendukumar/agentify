"""Epic 38, step 1: a live check that two tenants on the running stack
can't reach each other. Needs local sign-in (`.\\start.ps1`, not -Sso),
which also creates the second tenant, northwind.

    python testing/tenancy/smoke_tenancy.py

Requests go to 127.0.0.1 with each tenant's host in the Host header:
browsers resolve *.localhost by themselves, but Python on Windows doesn't.
It signs in to meridian and northwind, creates a process in each, tries
meridian's ids from northwind, and deletes what it created. Standard
library only.
"""

from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000"
ALL_ROLES = ["analyst", "architect", "process_owner", "approver", "auditor", "admin"]
results: list[tuple[bool, str]] = []


class Tenant:
    def __init__(self, host: str) -> None:
        self.host = host
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf: str | None = None

    def call(self, method: str, path: str, body=None, cookie: str | None = None) -> tuple[int, object]:
        headers = {"Host": f"{self.host}:8000", "Content-Type": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if cookie:
            headers["Cookie"] = cookie
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
        opener = urllib.request.build_opener() if cookie else self.opener
        try:
            with opener.open(request, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw.decode(errors="replace")

    def sign_in(self, name: str) -> dict:
        status, body = self.call("POST", "/api/auth/login", {"name": name, "roles": ALL_ROLES})
        if status != 200:
            sys.exit(f"Couldn't sign in to {self.host} ({status}: {body}). Is the stack running with local sign-in?")
        self.csrf = body["csrf_token"]
        return body

    def session_cookie(self) -> str:
        return next(f"{c.name}={c.value}" for c in self.jar if c.name == "asg_session")


def check(ok: bool, label: str, detail: object = "") -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f"  ({detail})" if detail != "" else ""))


def main() -> None:
    status, health = Tenant("localhost").call("GET", "/healthz")
    check(status == 200 and health.get("tenant_isolation") == "enforced", "the API says tenant isolation is enforced", health)
    status, body = Tenant("nobody.localhost").call("GET", "/api/processes")
    check(status == 404 and body == {"detail": "Not found"}, "an unknown host gets a plain 404", status)

    meridian, northwind = Tenant("meridian.localhost"), Tenant("northwind.localhost")
    meridian_user = meridian.sign_in("Isolation Probe")
    northwind_user = northwind.sign_in("Isolation Probe")
    check(meridian_user["id"] != northwind_user["id"], "the same name is a different user in each tenant")

    _, mine = meridian.call("POST", "/api/processes", {"name": "Isolation probe (meridian)"})
    _, theirs = northwind.call("POST", "/api/processes", {"name": "Isolation probe (northwind)"})
    try:
        _, listed = northwind.call("GET", "/api/processes")
        names = [p["name"] for p in listed]
        check("Isolation probe (meridian)" not in names and "Isolation probe (northwind)" in names,
              "northwind lists only its own processes", f"{len(names)} listed")
        for method, path, body in [
            ("GET", f"/api/processes/{mine['id']}", None),
            ("GET", f"/api/processes/{mine['id']}/datasets", None),
            ("PUT", f"/api/processes/{mine['id']}/systems/CRM", {}),
            ("DELETE", f"/api/processes/{mine['id']}", None),
            ("GET", f"/api/users/{meridian_user['id']}/sessions", None),
        ]:
            status, _ = northwind.call(method, path, body)
            check(status == 404, f"northwind gets 404 for {method} {path.replace(mine['id'], '<meridian process>').replace(meridian_user['id'], '<meridian user>')}", status)
        status, _ = northwind.call("GET", "/api/auth/me", cookie=meridian.session_cookie())
        check(status == 401, "meridian's session cookie is refused on northwind's host", status)
        status, still = meridian.call("GET", f"/api/processes/{mine['id']}")
        check(status == 200 and still["name"] == "Isolation probe (meridian)", "meridian's process is untouched")
    finally:
        meridian.call("DELETE", f"/api/processes/{mine['id']}")
        northwind.call("DELETE", f"/api/processes/{theirs['id']}")

    passed = sum(ok for ok, _ in results)
    print(f"{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
