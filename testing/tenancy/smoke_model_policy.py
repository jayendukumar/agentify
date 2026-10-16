"""Epic 38, US38.11: a live check of per-tenant registries and the model
provider key. Needs the stack with local sign-in (`.\\start.ps1`) and an
OPENROUTER_API_KEY; makes two real, tiny model calls (a fraction of a cent).

    python testing/tenancy/smoke_model_policy.py

- Northwind sets its own registries; Meridian's are unchanged, and an agent
  pushed to Northwind's registry can't be found from Meridian.
- Northwind switches to its own provider key. Inside the API container: while
  the secret isn't set, its calls are refused, not put on the platform's
  key; with it set (to the same OpenRouter key, for the test) a call goes
  out on Northwind's key and is recorded as Northwind's, on its own key.

Restores Northwind's settings and removes what it pushed.
"""

from __future__ import annotations

import subprocess
import sys

import httpx

API = "http://127.0.0.1:8000"
ALL_ROLES = ["analyst", "architect", "process_owner", "approver", "auditor", "admin"]
results: list[tuple[bool, str]] = []

# Runs inside the API container: the tenant's key is set for this process
# only, to the platform's own value, so a real call can go out on it.
IN_CONTAINER = r'''
import asyncio, os, sys
from sqlalchemy import select, text
from app.db.session import get_session_factory
from app.db.models import LlmUsageEventModel, TenantModel
from app.llm.client import LLMClient
from app.llm.types import ChatMessage
from app.tenancy import tenant_scope

from app.llm.exceptions import LLMPolicyError

with get_session_factory()() as s:
    tid = s.scalar(select(TenantModel.id).where(TenantModel.slug == "northwind"))
llm = LLMClient()
with tenant_scope(tid):
    try:
        asyncio.run(llm.complete([ChatMessage(role="user", content="hi")], operation="smoke_own_key"))
        print("missing called")
    except LLMPolicyError as exc:
        print("missing refused", "own model provider key" in str(exc))
os.environ["TENANT_NORTHWIND_LLM_PROVIDER_KEY"] = os.environ["OPENROUTER_API_KEY"]
with tenant_scope(tid):
    client, payer = llm._client_and_payer(None)
    print("payer", payer, "own_client", client is not llm._client)
    result = asyncio.run(llm.complete([ChatMessage(role="user", content="Reply with the word ok.")],
                                      operation="smoke_own_key", max_tokens=200))
    print("answered", bool(result.text or result.finish_reason))
    with get_session_factory()() as s:
        row = s.scalars(select(LlmUsageEventModel).where(LlmUsageEventModel.operation == "smoke_own_key")
                        .order_by(LlmUsageEventModel.id.desc())).first()
        print("recorded", row.key_source if row else None)
'''


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def client(host: str) -> httpx.Client:
    return httpx.Client(base_url=API, headers={"Host": f"{host}:8000"}, timeout=90)


def sign_in(c: httpx.Client, name: str) -> None:
    response = c.post("/api/auth/login", json={"name": name, "roles": ALL_ROLES})
    if response.status_code != 200:
        sys.exit(f"Couldn't sign in at {c.headers['Host']} ({response.status_code}). Is the stack running with local sign-in?")
    c.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def main() -> None:
    nw, mer = client("northwind.localhost"), client("meridian.localhost")
    sign_in(nw, "smoke-model-policy")
    sign_in(mer, "smoke-model-policy")
    registries_before = nw.get("/api/organization/registries").json()
    key_before = nw.get("/api/organization/llm-provider-key").json()
    meridian_registries = [r["name"] for r in mer.get("/api/registries").json()]
    try:
        # ---- registries ------------------------------------------------------------
        saved = nw.put("/api/organization/registries", json={"registries": [{"name": "smoke-claims", "type": "local"}]})
        check(saved.status_code == 200 and saved.json()["source"] == "tenant", "Northwind saved its own registry")
        check([r["name"] for r in nw.get("/api/registries").json()] == ["smoke-claims"], "Northwind publishes to it")
        check([r["name"] for r in mer.get("/api/registries").json()] == meridian_registries,
              f"Meridian's registries are unchanged ({meridian_registries})")
        pushed = nw.post("/api/registries/smoke-claims/push",
                         json={"agent_name": "Smoke Northwind Agent", "definition": {"smoke": True}, "tags": ["smoke"]})
        check(pushed.status_code == 200, f"pushed an agent to Northwind's registry ({pushed.status_code})")
        found_nw = nw.get("/api/registries/search", params={"q": "Smoke Northwind"}).json()["entries"]
        found_mer = mer.get("/api/registries/search", params={"q": "Smoke Northwind"}).json()["entries"]
        check(pushed.json()["id"] in {e["id"] for e in found_nw} and not found_mer, "Northwind finds it; Meridian doesn't")

        # ---- the provider key ------------------------------------------------------
        refused = nw.put("/api/organization/llm-provider-key", json={"key_ref": "env:OPENROUTER_API_KEY"})
        check(refused.status_code == 400, "Northwind can't use the platform's key as its 'own'")
        own = nw.put("/api/organization/llm-provider-key", json={"key_ref": "secret://tenant/llm_provider_key"}).json()
        check(own["key_set"] is False and own["location"].startswith("TENANT_NORTHWIND_LLM_PROVIDER_KEY"),
              f"its own key is referenced and not set yet ({own['location']})")
        out = subprocess.run(["docker", "exec", "-i", "axyntro-api-1", "python", "-"], input=IN_CONTAINER,
                             capture_output=True, text=True, check=False)
        lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
        check(lines.get("missing") == "refused True",
              f"while it isn't set, a call is refused, not put on the platform's key ({lines.get('missing')})")
        check(lines.get("payer") == "tenant own_client True", f"with the key set, calls go out on Northwind's key ({lines.get('payer')})")
        check(lines.get("answered") == "True", "the provider answered on it" + ("" if out.returncode == 0 else f": {out.stderr[-300:]}"))
        check(lines.get("recorded") == "tenant", "and the usage is Northwind's, on its own key")
        usage = nw.get("/api/usage").json()
        check(usage["budget"]["own_key"] is True, "the Usage page says the budget no longer limits it")
    finally:
        nw.put("/api/organization/llm-provider-key", json={"key_ref": key_before["key_ref"]})
        restore = registries_before["registries"] if registries_before["source"] == "tenant" else []
        nw.put("/api/organization/registries", json={"registries": restore})
        # Registries have no delete; remove the smoke test's entries directly.
        subprocess.run(["docker", "exec", "axyntro-db-1", "psql", "-U", "agentic", "-d", "agentic_solution_generator",
                        "-qc", "DELETE FROM registry_entries WHERE registry_name = 'smoke-claims'"], check=False)

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
