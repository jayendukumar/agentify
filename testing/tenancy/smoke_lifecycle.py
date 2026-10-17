"""Epic 38, US38.13: a live check of suspending, exporting and deleting a
tenant. Needs the stack with local sign-in (`.\\start.ps1`) and the
database on 127.0.0.1:5433 (as docker-compose.yml publishes it).

    python testing/tenancy/smoke_lifecycle.py

Creates a tenant unique to this run and hands it to its first admin, who
adds a process and a document and exports everything (the job worker in
the API makes the archive). Then the operator suspends it, deletes it,
restores it, deletes it again and -- with the waiting period moved into
the past directly in the database, as if 30 days had gone by -- erases
it. The database is then checked as its owner, which row-level security
doesn't restrict: no row of the tenant is left in any table.

Also cleans up tenants earlier smoke tests left in `deleting`.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import zipfile
from urllib.parse import urlsplit

import httpx
import psycopg

API = "http://127.0.0.1:8000"
OWNER_DSN = "postgresql://agentic:agentic@127.0.0.1:5433/agentic_solution_generator"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def client(host: str) -> httpx.Client:
    # Python on Windows doesn't resolve *.localhost; the Host header picks the tenant.
    return httpx.Client(base_url=API, headers={"Host": f"{host}:8000"}, timeout=60)


def docx_bytes() -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    document.add_paragraph("Invoices are approved by the finance lead within two days.")
    document.save(buffer)
    return buffer.getvalue()


def rows_left(tenant_id: str) -> dict[str, int]:
    """As the tables' owner (a superuser here): every table with a tenant_id."""
    with psycopg.connect(OWNER_DSN) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND column_name = 'tenant_id' AND table_name NOT IN ('tenant_domains', 'platform_audit_events')")]
        left = {}
        for table in tables:
            n = conn.execute(f'SELECT count(*) FROM "{table}" WHERE tenant_id = %s', (tenant_id,)).fetchone()[0]
            if n:
                left[table] = n
        return left


def pass_the_wait(tenant_id: str) -> None:
    with psycopg.connect(OWNER_DSN) as conn:
        conn.execute("UPDATE tenants SET delete_after = now() AT TIME ZONE 'utc' - interval '1 minute' WHERE id = %s",
                     (tenant_id,))


def files_exist(tenant_id: str) -> bool:
    found = subprocess.run(["docker", "compose", "exec", "-T", "api", "test", "-d", f".data/uploads/tenants/{tenant_id}"],
                           capture_output=True)
    return found.returncode == 0


def main() -> None:
    run = time.strftime("%H%M%S")
    ops = client("ops.localhost")
    response = ops.post("/api/operator/login", json={"name": "Smoke Operator", "email": "smoke@hosting.test"})
    if response.status_code != 200:
        sys.exit(f"Couldn't sign in to the console ({response.status_code}). Is the stack running with local sign-in?")
    ops.headers["X-CSRF-Token"] = response.json()["csrf_token"]

    slug, host = f"smoke-life-{run}", f"smoke-life-{run}.localhost"
    created = ops.post("/api/operator/tenants", json={"slug": slug, "display_name": "Smoke Lifecycle", "host": host,
                                                     "first_admin_email": "ada@smoke-life.test"})
    if created.status_code != 201:
        sys.exit(f"Couldn't create {slug}: {created.text}")
    tenant_id = created.json()["tenant"]["id"]
    url = urlsplit(created.json()["invitation_url"])
    admin = client(host)
    admin.get(f"{url.path}?{url.query}", follow_redirects=False)
    me = admin.get("/api/auth/me").json()
    admin.headers["X-CSRF-Token"] = me["csrf_token"]
    check(me.get("name") == "ada@smoke-life.test", "the first admin is in")

    # ---- the tenant's data ------------------------------------------------------------
    # Admin alone doesn't edit processes (Epic 37): the admin makes itself an analyst too.
    admin.put(f"/api/users/{me['id']}/roles", json={"roles": [{"role": "admin"}, {"role": "analyst"}]})
    process = admin.post("/api/processes", json={"name": "Smoke invoicing"}).json()
    uploaded = admin.post(f"/api/processes/{process['id']}/documents",
                          files={"files": ("invoicing.docx", docx_bytes(),
                                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    check(uploaded.status_code == 201, f"a process and a document ({uploaded.status_code})")

    # ---- export -----------------------------------------------------------------------
    requested = admin.post("/api/organization/exports")
    check(requested.status_code == 202, f"the admin asks for an export ({requested.status_code})")
    export = requested.json()
    for _ in range(60):
        export = next(e for e in admin.get("/api/organization/exports").json() if e["id"] == export["id"])
        if export["status"] not in ("queued", "running"):
            break
        time.sleep(1)
    check(export["status"] == "ready", f"the job made it ({export['status']}, {export.get('rows')} rows, "
                                       f"{export.get('file_count')} files)")
    download = admin.get(f"/api/organization/exports/{export['id']}/download")
    check(download.status_code == 200, "it downloads")
    archive = zipfile.ZipFile(io.BytesIO(download.content))
    manifest = json.loads(archive.read("manifest.json"))
    names = archive.namelist()
    check(manifest["tenant_id"] == tenant_id and manifest["schema_revision"], "the manifest names the tenant and schema")
    check([p["name"] for p in json.loads(archive.read("tables/processes.json"))] == ["Smoke invoicing"],
          "its processes, and only its")
    check(any(n.startswith(f"files/{process['id']}/") and n.endswith("invoicing.docx") for n in names), "its files")
    check(any(e["action"] == "first_admin_joined" for e in json.loads(archive.read("tables/audit_events.json"))),
          "its audit log")
    check(admin.get(f"/api/organization/exports/{export['id']}/download").status_code == 410, "only once")

    # ---- suspend ----------------------------------------------------------------------
    suspended = ops.post(f"/api/operator/tenants/{tenant_id}/suspend").json()
    check(suspended["status"] == "suspended", "the operator suspends it")
    check(admin.get("/api/processes").status_code == 403, "its signed-in admin is refused")
    check(client(host).get("/scim/v2/Users", headers={"Authorization": "Bearer x"}).status_code == 403, "SCIM too")
    listed = {t["id"]: t for t in ops.get("/api/operator/tenants").json()}
    check(listed[tenant_id]["last_export"]["status"] == "downloaded", "the console sees the export was taken")

    # ---- delete, wait, erase ------------------------------------------------------------
    deleting = ops.post(f"/api/operator/tenants/{tenant_id}/delete").json()
    check(deleting["status"] == "deleting" and deleting["delete_after"], f"deleted: erasable from {deleting['delete_after']}")
    early = ops.post(f"/api/operator/tenants/{tenant_id}/erase", json={"confirm_slug": slug})
    check(early.status_code == 409, "not erased before the waiting period ends")
    check(ops.post(f"/api/operator/tenants/{tenant_id}/restore").json()["status"] == "suspended", "restore undoes it")
    ops.post(f"/api/operator/tenants/{tenant_id}/delete")
    pass_the_wait(tenant_id)
    check(files_exist(tenant_id), "its files are there before erasing")
    erased = ops.post(f"/api/operator/tenants/{tenant_id}/erase", json={"confirm_slug": slug})
    check(erased.status_code == 200, f"erased ({erased.status_code})")
    cert = erased.json().get("certificate", {})
    check(cert.get("rows_total", 0) > 0 and cert.get("files_removed") == 1,
          f"the certificate counts {cert.get('rows_total')} rows and {cert.get('files_removed')} file(s)")
    check(rows_left(tenant_id) == {}, f"no row of the tenant is left in any table ({rows_left(tenant_id)})")
    check(not files_exist(tenant_id), "nor its files")
    check(tenant_id not in {t["id"] for t in ops.get("/api/operator/tenants").json()}, "it's gone from the console")
    check(client(host).get("/api/auth/config").status_code == 404, "its host reaches nothing")
    certificates = ops.get("/api/operator/deletion-certificates").json()
    check(any(c["tenant_id"] == tenant_id for c in certificates), "the certificate is in the platform log")
    platform = [e for e in ops.get("/api/operator/audit?limit=1000").json() if e["tenant_id"] == tenant_id]
    check("ada@smoke-life.test" not in json.dumps([e["detail"] for e in platform if e["action"] != "tenant_erased"]),
          "and the platform log no longer names its people")

    # ---- earlier smoke tenants left in `deleting` -----------------------------------------
    for tenant in ops.get("/api/operator/tenants").json():
        if tenant["status"] == "deleting" and tenant["slug"].startswith("smoke-"):
            pass_the_wait(tenant["id"])
            done = ops.post(f"/api/operator/tenants/{tenant['id']}/erase", json={"confirm_slug": tenant["slug"]})
            print(f"     cleaned up {tenant['slug']} ({done.status_code})")

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
