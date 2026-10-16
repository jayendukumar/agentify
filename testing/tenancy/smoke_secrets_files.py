"""Epic 38, US38.10: a live check that tenants' secrets, files and log
lines stay apart. Needs the stack with local sign-in (`.\\start.ps1`);
reads the API container's files and log through `docker`. No LLM calls
that succeed: the uploaded "document" isn't a real one.

    python testing/tenancy/smoke_secrets_files.py

Northwind (not the first tenant) can't point a connector, its model
policy or its IdP at an environment variable such as the platform's model
key; a `secret://tenant/...` reference resolves in Northwind's own store.
Uploads land under `tenants/<id>/`, whatever the file calls itself, and
the API log names the tenant on every line. Deletes what it created.
"""

from __future__ import annotations

import io
import subprocess
import sys
import time

import httpx
from docx import Document

API = "http://127.0.0.1:8000"
ALL_ROLES = ["analyst", "architect", "process_owner", "approver", "auditor", "admin"]
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
UPLOADS = "/app/.data/uploads"
results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def client(host: str) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Host": f"{host}:8000"}, timeout=60)


def sign_in(c: httpx.Client, path: str, body: dict) -> dict:
    response = c.post(path, json=body)
    if response.status_code != 200:
        sys.exit(f"Couldn't sign in at {c.headers['Host']}{path} ({response.status_code}: {response.text}). "
                 "Is the stack running with local sign-in?")
    c.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()


def in_container(command: str) -> str:
    return subprocess.run(["docker", "exec", "axyntro-api-1", "sh", "-c", command],
                          capture_output=True, text=True, check=False).stdout


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    Document().save(buffer)
    return buffer.getvalue()


def main() -> None:
    ops = client("ops.localhost")
    sign_in(ops, "/api/operator/login", {"name": "Smoke Operator", "email": "smoke@hosting.test"})
    tenants = {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}
    northwind_id = tenants["northwind"]["id"]

    nw = client("northwind.localhost")
    sign_in(nw, "/api/auth/login", {"name": "smoke-secrets", "roles": ALL_ROLES})
    mer = client("meridian.localhost")
    sign_in(mer, "/api/auth/login", {"name": "smoke-secrets", "roles": ALL_ROLES})

    created: list[tuple[httpx.Client, str]] = []
    policy_before = nw.get("/api/organization").json().get("profile", {}).get("llm_policy")
    try:
        # ---- secrets ---------------------------------------------------------------
        http_config = {"url": "https://cmdb.northwind.example/api", "kind": "application", "records_key": "result",
                       "mapping": {"external_id": "id", "name": "name"}}
        refused = nw.post("/api/organization/connectors", json={
            "name": "smoke-env", "type": "http", "config": {**http_config, "token_env": "OPENROUTER_API_KEY"}})
        check(refused.status_code == 400, f"Northwind can't name the platform's key as a connector token ({refused.status_code})")
        saved = nw.post("/api/organization/connectors", json={
            "name": "smoke-ref", "type": "http", "config": {**http_config, "token_ref": "secret://tenant/smoke_token"}})
        check(saved.status_code == 201, f"a secret://tenant/ reference is accepted ({saved.status_code})")
        if saved.status_code == 201:
            created.append((nw, f"/api/organization/connectors/{saved.json()['id']}"))
            nw.post(f"/api/organization/connectors/{saved.json()['id']}/sync")
            error = ""
            for _ in range(20):
                runs = nw.get(f"/api/organization/connectors/{saved.json()['id']}/runs").json()
                error = next((r["error"] for r in runs if r.get("error")), "")
                if error:
                    break
                time.sleep(1)
            check("TENANT_NORTHWIND_SMOKE_TOKEN" in error,
                  f"syncing looks for Northwind's own TENANT_NORTHWIND_SMOKE_TOKEN ({error[:90]!r})")
        first = mer.post("/api/organization/connectors", json={
            "name": "smoke-env", "type": "http", "config": {**http_config, "token_env": "SMOKE_TOKEN"}})
        check(first.status_code == 201, f"Meridian, the first tenant, keeps environment names ({first.status_code})")
        if first.status_code == 201:
            created.append((mer, f"/api/organization/connectors/{first.json()['id']}"))

        policy = {"base_url": "https://llm.northwind.example/v1", "provider_name": "self_hosted"}
        leaked = nw.put("/api/organization/llm-policy", json={**policy, "api_key_env": "OPENROUTER_API_KEY"})
        check(leaked.status_code == 400, f"Northwind's model endpoint can't use the platform's key ({leaked.status_code})")
        idp = nw.put("/api/directory/identity", json={
            "auth_mode": "oidc", "issuer": "http://localhost:8180/realms/northwind", "client_id": "axyntro",
            "client_secret_ref": "env:OPENROUTER_API_KEY", "acr_values": "mfa", "mfa_acr_values": ["mfa"]})
        check(idp.status_code == 400 and "first organization" in idp.text,
              f"nor send it to an IdP as its client secret ({idp.status_code})")
        identity = nw.get("/api/directory/identity").json()
        check(identity["secret_location"].startswith("TENANT_NORTHWIND_OIDC_CLIENT_SECRET"),
              f"the Directory page says where the secret is kept ({identity['secret_location']})")

        # ---- files -----------------------------------------------------------------
        process = nw.post("/api/processes", json={"name": "Smoke: files"}).json()
        created.append((nw, f"/api/processes/{process['id']}"))
        for name in ("expenses.docx", "../../../../tmp/escaped.docx"):
            uploaded = nw.post(f"/api/processes/{process['id']}/documents", files={"files": (name, docx_bytes(), DOCX)})
            check(uploaded.status_code == 201, f"uploaded {name!r} ({uploaded.status_code})")
        stored = in_container(f"ls {UPLOADS}/tenants/{northwind_id}/{process['id']}").split()
        check(len(stored) == 2 and any(s.endswith("_expenses.docx") for s in stored)
              and any(s.endswith("_escaped.docx") for s in stored),
              f"both files are in tenants/{northwind_id}/{process['id']}/ ({stored})")
        check(not in_container("ls /tmp/escaped.docx 2>/dev/null").strip(), "nothing escaped to /tmp")
        check(not in_container(f"ls -d {UPLOADS}/{process['id']} 2>/dev/null").strip(), "nothing at the old shared level")

        # ---- logs ------------------------------------------------------------------
        time.sleep(2)
        log = subprocess.run(["docker", "logs", "--since", "2m", "axyntro-api-1"], capture_output=True, text=True).stderr
        tagged = [line for line in log.splitlines() if f"[tenant={northwind_id}]" in line]
        check(bool(tagged), f"the API log names Northwind on its lines ({len(tagged)} lines)")
    finally:
        for c, path in reversed(created):
            c.delete(path)
        if policy_before is not None:
            nw.put("/api/organization/llm-policy", json=policy_before)

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
