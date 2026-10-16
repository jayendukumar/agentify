"""Epic 38, US38.9: a live check of per-tenant usage and the monthly budget,
with real LLM calls. Needs the stack with local sign-in (`.\\start.ps1`)
and an OPENROUTER_API_KEY; makes two small document extractions in
Northwind (a fraction of a cent).

    python testing/tenancy/smoke_usage.py

An operator (local sign-in on ops.localhost) and Northwind's admin. The
first extraction is recorded as Northwind's usage and nobody else's; then
the operator sets Northwind's budget below what it has spent, and the
second extraction is refused before it reaches the model. Restores
Northwind's limits and deletes what it created.
"""

from __future__ import annotations

import io
import sys
import time

import httpx
from docx import Document

API = "http://127.0.0.1:8000"
ALL_ROLES = ["analyst", "architect", "process_owner", "approver", "auditor", "admin"]
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def client(host: str) -> httpx.Client:
    # Python on Windows doesn't resolve *.localhost; the Host header picks the tenant.
    return httpx.Client(base_url=API, headers={"Host": f"{host}:8000"}, timeout=60)


def sign_in(c: httpx.Client, path: str, body: dict) -> None:
    response = c.post(path, json=body)
    if response.status_code != 200:
        sys.exit(f"Couldn't sign in at {c.headers['Host']}{path} ({response.status_code}: {response.text}). "
                 "Is the stack running with local sign-in?")
    c.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def docx_bytes() -> bytes:
    doc = Document()
    doc.add_paragraph("Expense claims: an employee submits a claim with receipts. "
                      "The line manager approves claims under 500 EUR; finance approves larger ones. "
                      "Finance pays approved claims in the next payroll run.")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def extract(c: httpx.Client, process_id: str) -> dict:
    files = {"files": ("expenses.docx", docx_bytes(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    uploaded = c.post(f"/api/processes/{process_id}/documents", files=files)
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()[0]["id"]
    for _ in range(90):
        document = c.get(f"/api/processes/{process_id}/documents/{document_id}").json()
        if document["status"] in ("done", "failed"):
            return document
        time.sleep(1)
    return document


def main() -> None:
    ops = client("ops.localhost")
    sign_in(ops, "/api/operator/login", {"name": "Smoke Operator", "email": "smoke@hosting.test"})
    tenants = {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}
    northwind, meridian = tenants["northwind"], tenants["meridian"]
    original = northwind["limits"]

    nw = client("northwind.localhost")
    sign_in(nw, "/api/auth/login", {"name": "smoke-usage", "roles": ALL_ROLES})
    mer = client("meridian.localhost")
    sign_in(mer, "/api/auth/login", {"name": "smoke-usage", "roles": ALL_ROLES})

    before_nw = nw.get("/api/usage").json()
    before_mer = mer.get("/api/usage").json()
    calls = lambda overview: overview["months"][0]["calls"] if overview["months"] else 0  # noqa: E731
    cost = lambda overview: overview["budget"]["spent_usd"]  # noqa: E731

    process = nw.post("/api/processes", json={"name": "Smoke: usage"}).json()
    try:
        document = extract(nw, process["id"])
        check(document["status"] == "done", f"a real extraction in Northwind finished ({document['status']})")
        after_nw = nw.get("/api/usage").json()
        check(calls(after_nw) > calls(before_nw), f"Northwind's calls went up ({calls(before_nw)} -> {calls(after_nw)})")
        check(cost(after_nw) > cost(before_nw), f"and its cost (${cost(before_nw):.6f} -> ${cost(after_nw):.6f})")
        ops_view = {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}["northwind"]["usage"]
        check(ops_view["llm_calls_this_month"] == calls(after_nw), "the operator console shows the same count")
        after_mer = mer.get("/api/usage").json()
        check(calls(after_mer) == calls(before_mer), "Meridian's usage didn't change")

        # A budget below what Northwind has spent: the next call is refused.
        budget = round(max(cost(after_nw) / 2, 0.000001), 6)
        limited = ops.put(f"/api/operator/tenants/{northwind['id']}/limits",
                          json={**original, "llm_monthly_budget_usd": budget})
        check(limited.status_code == 200, f"the operator set Northwind's budget to ${budget}")
        time.sleep(31)  # the API reads a tenant's limits at most every 30 seconds
        overview = nw.get("/api/usage").json()
        check(overview["budget"]["status"] == "stopped", f"Northwind's usage page says stopped ({overview['budget']['status']})")
        refused = extract(nw, process["id"])
        check(refused["status"] == "failed", f"the next extraction failed ({refused['status']})")
        check(calls(nw.get("/api/usage").json()) == calls(after_nw), "and never reached the model (no new call)")
        audit = nw.get("/api/audit-events", params={"action": "llm_budget_reached"})
        if audit.status_code == 200:
            check(any(e["action"] == "llm_budget_reached" for e in audit.json()), "the tenant's audit log says why")
        check(mer.get("/api/usage").json()["budget"]["status"] != "stopped", "Meridian is unaffected")
        check(any(e["action"] == "tenant_limits_changed" for e in ops.get("/api/operator/audit").json()),
              "the change is in the platform audit log")
    finally:
        ops.put(f"/api/operator/tenants/{northwind['id']}/limits", json=original)
        nw.delete(f"/api/processes/{process['id']}")
    del meridian

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
