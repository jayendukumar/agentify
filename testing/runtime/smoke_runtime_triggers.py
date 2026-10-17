"""Epic 39, US39.4: a live check of the ways to start an agent.

Same setup as smoke_runtime.py (the stack with local sign-in, an
OPENROUTER_API_KEY, Keycloak with the `core-banking` realm, the stand-in):

    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime_triggers.py

What it does, as Meridian, on an active deployment of the loan onboarding
agent (built the way smoke_runtime.py builds it):

1. A runtime client limited to this deployment; an unknown deployment in
   the limit is refused.
2. Input of the wrong type (a number where the schema says string) is
   refused with 422, before any model call.
3. A real case with a callback URL. The loan system's endpoint is down for
   the first try, so the callback is retried; the one delivered carries an
   HMAC signature that checks out with the client's signing secret.
4. A case started by an internal trigger through the job queue (a sweep),
   with no client, and the same key queued again starts nothing new.

Cleans up the process (and with it the deployment) afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_runtime as rt  # noqa: E402

CALLBACK = f"{rt.STANDIN_FROM_API}/hooks/agent-done"
TERMINAL = ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT")

SWEEP = r'''
import sys
from sqlalchemy import select
from app.db.models import TenantModel
from app.db.session import get_session_factory
from app.runtime import jobs
from app.tenancy import tenant_scope

deployment_id, key = sys.argv[1], sys.argv[2]
with get_session_factory()() as s:
    tid = s.scalar(select(TenantModel.id).where(TenantModel.slug == "meridian"))
with tenant_scope(tid), get_session_factory()() as s:
    jobs.queue_submission(s, deployment_id, data={"customerId": "C-100"}, idempotency_key=key,
                          trigger="smoke-sweep")
    s.commit()
print("ok")
'''


def sweep(deployment_id: str, key: str) -> None:
    out = subprocess.run(["docker", "exec", "-i", "axyntro-api-1", "python", "-", deployment_id, key], input=SWEEP,
                         capture_output=True, text=True, check=False)
    if out.stdout.strip() != "ok":
        sys.exit(f"Queueing the sweep failed: {out.stderr[-600:]}")


def main() -> None:
    rt.preflight()
    httpx.post(f"{rt.STANDIN}/_reset")
    arch = rt.session("rt-architect", ["architect", "analyst", "admin", "process_owner"])
    appr = rt.session("rt-approver", ["approver", "process_owner"])
    state: dict = {}
    client_id = None
    try:
        process_id, _, deployment, _ = rt.prepare(arch, appr, state, name="Smoke: ways to start an agent")
        check = rt.check

        # ---- 1. a client limited to this deployment ----------------------------------------
        unknown = arch.post("/api/runtime/clients", json={"label": "Smoke", "deployment_ids": ["dep_nowhere"]})
        check(unknown.status_code == 422, f"a client limited to an unknown deployment is refused ({unknown.status_code})")
        created = arch.post("/api/runtime/clients",
                            json={"label": "Smoke loan system", "deployment_ids": [deployment["id"]]}).json()
        client_id = created["id"]
        check(created["deployment_ids"] == [deployment["id"]] and created["signing_secret"].startswith("axs_"),
              "a client limited to this deployment, with a signing secret shown once")
        caller = httpx.Client(base_url=rt.API, timeout=60,
                              headers={"Host": "localhost:8000", "Authorization": f"Bearer {created['token']}"})

        # ---- 2. typed input ----------------------------------------------------------------
        wrong = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                            json={"input": {"customerId": 100}}, headers={"Idempotency-Key": "trg-0"})
        check(wrong.status_code == 422 and "must be text" in wrong.text,
              f"a number where the schema says string gets 422 ({wrong.status_code}: {wrong.json().get('detail')})")

        # ---- 3. a signed callback, retried -------------------------------------------------
        httpx.post(f"{rt.STANDIN}/_hooks_fail", json={"times": 1})
        started = caller.post(f"/runtime/v1/deployments/{deployment['id']}/executions",
                              json={"input": {"customerId": "C-100"}, "callback_url": CALLBACK},
                              headers={"Idempotency-Key": "trg-1"})
        check(started.status_code == 202, f"a case with a callback URL was accepted ({started.status_code})")
        done = rt.wait_for(lambda: caller.get(f"/runtime/v1/executions/{started.json()['id']}").json(),
                           lambda e: e["status"] in TERMINAL)
        check(done["status"] == "COMPLETED", f"the case completed: {done.get('final_output')}")
        hooks = rt.wait_for(lambda: httpx.get(f"{rt.STANDIN}/_hooks").json(), lambda h: len(h) >= 2, timeout=90)
        check(len(hooks) == 2, f"the loan system was down at first and the callback was retried ({len(hooks)} tries)")
        last = hooks[-1]
        body = json.loads(last["body"])
        check(body["execution_id"] == started.json()["id"] and body["status"] == "COMPLETED"
              and body["final_output"] == done["final_output"], f"the callback carries the result ({body})")
        parts = dict(p.split("=", 1) for p in (last["signature"] or "").split(","))
        expected = hmac.new(created["signing_secret"].encode(), f"{parts.get('t')}.".encode() + last["body"].encode(),
                            hashlib.sha256).hexdigest()
        check(hmac.compare_digest(parts.get("v1", ""), expected), "its signature checks out with the signing secret")

        # ---- 4. an internal trigger ---------------------------------------------------------
        sweep(deployment["id"], "sweep-1")
        executions_url = f"/api/processes/{process_id}/deployments/{deployment['id']}/executions"
        executions = rt.wait_for(lambda: arch.get(executions_url).json(),
                                 lambda xs: len(xs) == 2 and all(x["status"] in TERMINAL for x in xs))
        swept = next((x for x in executions if x["id"] != started.json()["id"]), None)
        detail = arch.get(f"/api/processes/{process_id}/executions/{swept['id']}").json() if swept else {}
        check(detail.get("trigger") == "smoke-sweep" and detail.get("client_id") is None
              and detail.get("status") == "COMPLETED",
              f"the sweep started a case through the queue ({detail.get('trigger')}, {detail.get('status')})")
        sweep(deployment["id"], "sweep-1")
        again = rt.wait_for(lambda: arch.get(executions_url).json(), lambda xs: len(xs) > 2, timeout=15)
        check(len(again) == 2, "the same sweep key queued again started nothing new")
        caller.close()
    finally:
        if client_id:
            arch.post(f"/api/runtime/clients/{client_id}/revoke")
        rt.cleanup(arch, state)

    failed = [what for ok, what in rt.results if not ok]
    print(f"\n{len(rt.results) - len(failed)}/{len(rt.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
