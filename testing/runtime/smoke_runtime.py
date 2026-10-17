"""Epic 39, build step 2: a live check of the agent runtime, end to end.

Needs the stack with local sign-in (`.\\start.ps1`), an OPENROUTER_API_KEY
(a few real model calls, a few cents), Keycloak with the `core-banking`
realm, and the production stand-in:

    docker compose -f docker-compose.yml -f docker-compose.sso.yml up -d keycloak
    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime.py

What it does, as Meridian:

1. Registers "Core Banking" in the catalog with the stand-in's OpenAPI
   spec, a sandbox endpoint and a production endpoint.
2. Builds a one-step process whose agent looks a customer up and opens an
   account, and tests it in the digital twin in API mode, against the
   sandbox.
3. Approves it, deploys it, binds Core Banking to production with OAuth
   client credentials from Keycloak, and activates it with someone else's
   approval.
4. Runs a real case through `/runtime/v1` with a runtime client token, and
   checks what production received: a Keycloak token issued to the agent,
   with the scopes bound, and an idempotency key on the write.
5. Kills the agent while a production call is in flight, and checks that
   the call finished, nothing ran after it, and new work is refused.

Cleans up the process (and with it the deployment) afterwards.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000"
STANDIN = "http://127.0.0.1:9100"
STANDIN_FROM_API = "http://host.docker.internal:9100"
TOKEN_URL = "http://keycloak:8080/realms/core-banking/protocol/openid-connect/token"
results: list[tuple[bool, str]] = []

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

SEED = r'''
import sys
from sqlalchemy import select
from app.db import repository
from app.db.models import TenantModel
from app.db.session import get_session_factory
from app.schemas.blueprint import AgentIOField, AgentSpec, BlueprintNodeResult
from app.tenancy import tenant_scope

process_id, step = sys.argv[1], sys.argv[2]
with get_session_factory()() as s:
    tid = s.scalar(select(TenantModel.id).where(TenantModel.slug == "meridian"))
with tenant_scope(tid), get_session_factory()() as s:
    if step == "gaps":
        repository.mark_gap_analysis_completed(s, process_id)
    else:
        version_id = sys.argv[3]
        spec = AgentSpec(
            name="Loan Onboarding Agent",
            purpose=("Look the applicant up in Core Banking. If their risk rating is low, open a savings account "
                     "for them in Core Banking and report its account id; otherwise open nothing."),
            trigger="A loan application is submitted",
            required_inputs=[AgentIOField(name="customerId", source_or_destination="Loan system", format="string")],
            expected_outputs=[
                AgentIOField(name="accountId", source_or_destination="Core Banking", format="string"),
                AgentIOField(name="decision", source_or_destination="Loan system", format="approve or reject"),
            ],
            tools_systems_needed=["Core Banking"],
            human_checkpoint=sys.argv[4] if len(sys.argv) > 4 else "none",
        )
        repository.set_blueprint_overlay(s, process_id, version_id, [BlueprintNodeResult(
            node_id="Task_a", verdict="automatable", step_type="data_retrieval_transformation",
            rationale="A rule-based lookup and account opening.", agent_spec=spec)])
    s.commit()
print("ok")
'''


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def session(name: str, roles: list[str]) -> httpx.Client:
    c = httpx.Client(base_url=API, headers={"Host": "localhost:8000"}, timeout=120)
    response = c.post("/api/auth/login", json={"name": name, "roles": roles})
    if response.status_code != 200:
        sys.exit(f"Couldn't sign in ({response.status_code}). Is the stack running with local sign-in?")
    c.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return c


def seed(*args: str) -> None:
    out = subprocess.run(["docker", "exec", "-i", "axyntro-api-1", "python", "-", *args], input=SEED,
                         capture_output=True, text=True, check=False)
    if out.stdout.strip() != "ok":
        sys.exit(f"Seeding {args[1]} failed: {out.stderr[-600:]}")


def wait_for(fetch, done, timeout: float = 240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fetch()
        if done(value):
            return value
        time.sleep(1)
    return value


def bpmn(process_id: str) -> str:
    from app.bpmn.builder import build_bpmn_xml
    from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef

    ref = SourceRef(document_id="d", location="p1", excerpt="x")
    schema = ProcessSchema(process_name="Loan onboarding", elements=[
        ProcessElement(id="start", type="start_event", label="Application received", source_refs=[ref], confidence="high"),
        ProcessElement(id="a", type="task", label="Open the account", source_refs=[ref], confidence="high"),
        ProcessElement(id="end", type="end_event", label="Done", source_refs=[ref], confidence="high"),
    ], flows=[ProcessFlow(id="f1", **{"from": "start"}, to="a"), ProcessFlow(id="f2", **{"from": "a"}, to="end")])
    return build_bpmn_xml(process_id, schema)[0]


def preflight() -> None:
    try:
        httpx.get(f"{STANDIN}/openapi-spec.json", timeout=3).raise_for_status()
    except httpx.HTTPError:
        sys.exit("Start the stand-in first: python testing/runtime/core_banking_standin.py")
    try:
        httpx.get("http://127.0.0.1:8180/realms/core-banking", timeout=3).raise_for_status()
    except httpx.HTTPError:
        sys.exit("Start Keycloak with the core-banking realm: docker compose -f docker-compose.yml "
                 "-f docker-compose.sso.yml up -d keycloak")


def prepare(arch: httpx.Client, appr: httpx.Client, state: dict, *, checkpoint: str = "none",
            name: str = "Smoke: loan onboarding") -> tuple[str, dict, dict, str]:
    """Steps 1-3: the catalog, the agent tested in the twin, and an active
    deployment bound to production. `state` collects what to clean up."""
    # ---- 1. the catalog ---------------------------------------------------------------
    app = state["app"] = arch.post("/api/organization/entities",
                                  json={"kind": "application", "name": "Core Banking"}).json()
    spec = httpx.get(f"{STANDIN}/openapi-spec.json").content
    arch.put(f"/api/organization/applications/{app['id']}/api-spec",
             files={"file": ("spec.json", spec, "application/json")}).raise_for_status()
    endpoints = arch.put(f"/api/organization/applications/{app['id']}/endpoints", json=[
        {"url": f"{STANDIN_FROM_API}/sandbox", "environment": "sandbox"},
        {"url": STANDIN_FROM_API, "environment": "production"},
    ])
    check(endpoints.status_code == 200, f"Core Banking registered with sandbox and production endpoints ({endpoints.status_code})")

    # ---- 2. the agent, tested in the twin --------------------------------------------
    process_id = state["process_id"] = arch.post("/api/processes", json={"name": name}).json()["id"]
    arch.put(f"/api/processes/{process_id}/bpmn", json={"xml": bpmn(process_id)}).raise_for_status()
    seed(process_id, "gaps")
    version = arch.post(f"/api/processes/{process_id}/finalize").json()
    appr.post(f"/api/processes/{process_id}/versions/{version['id']}/approve", json={}).raise_for_status()
    seed(process_id, "blueprint", version["id"], checkpoint)
    arch.put(f"/api/processes/{process_id}/systems/Core Banking", json={"application_id": app["id"]}).raise_for_status()
    artifact = arch.post(f"/api/processes/{process_id}/blueprint/nodes/Task_a/agent-artifact").json()
    contract = artifact["definition"]["tool_contracts"][0]
    check(contract["application_id"] == app["id"] and contract["action"] == "update",
          f"the agent's Core Banking contract is linked and may update ({contract['action']})")

    scenario = arch.post(f"/api/processes/{process_id}/agent-artifacts/{artifact['id']}/scenarios", json={
        "name": "Low-risk applicant", "inputs": {"customerId": "C-100"},
        "system_stubs": {"Core Banking": {"mode": "api"}},
    }).json()
    run = arch.post(f"/api/processes/{process_id}/scenarios/{scenario['id']}/run").json()
    run = wait_for(lambda: arch.get(f"/api/processes/{process_id}/twin-runs/{run['id']}").json(),
                   lambda r: r["status"] not in ("QUEUED", "RUNNING", "WAITING_FOR_MODEL", "WAITING_FOR_TOOL"))
    sandbox_calls = [c for c in httpx.get(f"{STANDIN}/_calls").json() if c["environment"] == "sandbox"]
    check(run["status"] == "COMPLETED" and sandbox_calls,
          f"twin run in API mode {run['status']}, {len(sandbox_calls)} sandbox call(s), output {run.get('final_output')}")

    # ---- 3. deploy, bind, activate ---------------------------------------------------
    appr.post(f"/api/processes/{process_id}/blueprint/agent-artifacts/{artifact['id']}/approve").raise_for_status()
    deployment = arch.post(f"/api/processes/{process_id}/agent-artifacts/{artifact['id']}/deployments").json()
    check(deployment.get("status") == "pending" and deployment["bindings"][0]["base_url"] == STANDIN_FROM_API,
          f"deployed as pending, bound to the production endpoint ({deployment.get('readiness')})")
    dep = f"/api/processes/{process_id}/deployments/{deployment['id']}"
    bound = arch.put(f"{dep}/bindings/Core Banking", json={
        "base_url": STANDIN_FROM_API, "operations": ["getCustomer", "openAccount"],
        "credential": {"kind": "oauth_client_credentials", "token_url": TOKEN_URL, "client_id": "agent-loans",
                       "secret_ref": "secret://tenant/core_banking_secret",
                       "scopes": {"update": ["core.read", "core.write"]}},
    })
    check(bound.status_code == 200, f"bound with OAuth client credentials ({bound.status_code} {bound.text[:200]})")
    tested = arch.post(f"{dep}/bindings/Core Banking/test-credential").json()
    check(tested.get("ok") is True, f"Keycloak issued the agent a token ({tested})")
    own = arch.post(f"{dep}/activate", json={"shadow_waiver": "Smoke test"})
    check(own.status_code == 403, "the architect who asked for it can't activate it")
    no_waiver = appr.post(f"{dep}/activate", json={})
    check(no_waiver.status_code == 409 and "shadow" in no_waiver.text, "a data-changing agent needs shadow or a waiver")
    active = appr.post(f"{dep}/activate", json={"shadow_waiver": "Smoke test against a stand-in"})
    check(active.status_code == 200 and active.json()["status"] == "active", "an approver activated it")

    return process_id, app, deployment, dep


def cleanup(arch: httpx.Client, state: dict) -> None:
    httpx.post(f"{STANDIN}/_reset")
    if state.get("process_id"):
        arch.delete(f"/api/processes/{state['process_id']}")
    if state.get("app"):
        # The catalog only retires entities; remove the smoke test's own row.
        subprocess.run(["docker", "exec", "axyntro-db-1", "psql", "-U", "agentic", "-d", "agentic_solution_generator",
                        "-qc", f"DELETE FROM context_entities WHERE id = '{state['app']['id']}'"], check=False)


def main() -> None:
    preflight()
    httpx.post(f"{STANDIN}/_reset")
    arch = session("rt-architect", ["architect", "analyst", "admin", "process_owner"])
    appr = session("rt-approver", ["approver", "process_owner"])
    state: dict = {}
    process_id = None
    try:
        process_id, app, deployment, dep = prepare(arch, appr, state)

        # ---- 4. a real case --------------------------------------------------------------
        runtime_client = arch.post("/api/runtime/clients", json={"label": "Smoke loan system"}).json()
        token = runtime_client["token"]
        caller = httpx.Client(base_url=API, headers={"Host": "localhost:8000", "Authorization": f"Bearer {token}"},
                              timeout=60)
        started = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                              json={"input": {"customerId": "C-100"}}, headers={"Idempotency-Key": "smoke-1"})
        check(started.status_code == 202, f"the loan system started a case ({started.status_code})")
        done = wait_for(lambda: caller.get(f"/runtime/v1/executions/{started.json()['id']}").json(),
                        lambda e: e["status"] in ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT"))
        check(done["status"] == "COMPLETED", f"the case completed: {done.get('final_output')} {done.get('deviations')}")
        production = [c for c in httpx.get(f"{STANDIN}/_calls").json() if c["environment"] == "production"]
        check(production and all(c["client"] == "agent-loans" for c in production),
              f"production got {len(production)} call(s), each with a token issued to agent-loans")
        check(all("core.write" in (c["scope"] or "") for c in production), "with the scopes the binding asks for")
        writes = [c for c in production if c["method"] == "POST"]
        check(bool(writes) and all(c["idempotency_key"] for c in writes), f"writes carry an idempotency key ({writes})")
        detail = arch.get(f"/api/processes/{process_id}/executions/{started.json()['id']}").json()
        check(detail["identity_id"] == deployment["identity"]["id"], "the execution is recorded as the agent's identity")
        again = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                            json={"input": {"customerId": "C-100"}}, headers={"Idempotency-Key": "smoke-1"})
        check(again.status_code == 200 and again.json()["id"] == started.json()["id"], "the same key is the same case")

        # ---- 5. the kill switch ----------------------------------------------------------
        httpx.post(f"{STANDIN}/_slow", json={"seconds": 8})
        second = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                             json={"input": {"customerId": "C-100"}}, headers={"Idempotency-Key": "smoke-2"}).json()
        in_flight = wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{second['id']}").json(),
                             lambda e: any(s["type"] == "POLICY_CHECK" for s in e["steps"]), timeout=120)
        check(any(s["type"] == "POLICY_CHECK" for s in in_flight["steps"]), "a production call is in flight")
        killed_at = time.monotonic()
        kill = arch.post(f"{dep}/kill", json={"reason": "Smoke test: wrong product"})
        refused = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                              json={"input": {"customerId": "C-200"}}, headers={"Idempotency-Key": "smoke-3"})
        check(kill.status_code == 200 and refused.status_code == 503 and time.monotonic() - killed_at < 5,
              f"after the kill, new work is refused at once ({refused.status_code}: {refused.json().get('detail')})")
        final = wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{second['id']}").json(),
                         lambda e: e["status"] in ("KILLED", "COMPLETED", "FAILED", "ESCALATED"), timeout=60)
        types = [s["type"] for s in final["steps"]]
        last_tool = max(i for i, t in enumerate(types) if t in ("TOOL_EXECUTED", "TOOL_FAILED"))
        check(final["status"] == "KILLED", f"the running case ended KILLED ({final['status']})")
        check("MODEL_INVOKED" not in types[last_tool:] and types[-1] == "RUN_COMPLETED",
              f"the in-flight call finished and nothing ran after it ({types[-4:]})")
        reactivate = appr.post(f"{dep}/activate", json={"shadow_waiver": "again"})
        check(reactivate.status_code == 409, "a killed deployment can't be activated again")
        caller.close()
        arch.post(f"/api/runtime/clients/{runtime_client['id']}/revoke")
    finally:
        cleanup(arch, state)

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
