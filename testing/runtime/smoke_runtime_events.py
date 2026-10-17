"""Epic 39, US39.16: a live check of an agent started by a signed event.

Same setup as smoke_runtime.py (the stack with local sign-in, an
OPENROUTER_API_KEY -- real model calls, a few cents -- Keycloak, the
stand-in). The loan system's signing secret is
TENANT_MERIDIAN_LOAN_EVENTS in docker-compose.yml.

    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime_events.py

What it does, as Meridian, on an active deployment of the loan onboarding
agent:

1. Subscribes the deployment to the loan system's "loan.submitted"
   events, mapping data.applicant.id onto the agent's customerId.
2. The loan system sends a signed event: the case runs with the mapped
   input and opens the account.
3. The same event delivered again starts nothing new; another event type
   is ignored; a forged, a replayed (10 minutes old) and an unsigned event
   are refused.

Cleans up the process (and with it the deployment) afterwards.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_runtime as rt  # noqa: E402

SECRET = "loan-events-dev-only-secret"
TERMINAL = ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT")


def send(url: str, event: dict, *, secret: str = SECRET, age: float = 0, signed: bool = True) -> httpx.Response:
    body = json.dumps(event).encode()
    ts = str(int(time.time() - age))
    headers = {"Host": "localhost:8000", "Content-Type": "application/json"}
    if signed:
        digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        headers.update({"X-Timestamp": ts, "X-Signature": f"sha256={digest}"})
    return httpx.post(f"{rt.API}{url}", content=body, headers=headers, timeout=30)


def main() -> None:
    rt.preflight()
    httpx.post(f"{rt.STANDIN}/_reset")
    arch = rt.session("rt-architect", ["architect", "analyst", "admin", "process_owner"])
    appr = rt.session("rt-approver", ["approver", "process_owner"])
    state: dict = {}
    check = rt.check
    try:
        process_id, _, deployment, dep = rt.prepare(arch, appr, state, name="Smoke: event trigger")
        created = arch.post(f"{dep}/event-subscriptions", json={
            "label": "Loan system", "secret_ref": "secret://tenant/loan_events",
            "input_mapping": {"customerId": "data.applicant.id"}, "event_filter": {"type": "loan.submitted"},
        })
        check(created.status_code == 201, f"subscribed to loan.submitted events ({created.status_code} {created.text[:160]})")
        url = created.json()["url"]
        event = {"id": f"evt-{int(time.time())}", "type": "loan.submitted", "data": {"applicant": {"id": "C-100"}}}

        started = send(url, event)
        check(started.status_code == 202, f"a signed event started a case ({started.status_code} {started.text[:160]})")
        execution_id = started.json().get("id")
        done = rt.wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{execution_id}").json(),
                           lambda e: e["status"] in TERMINAL)
        check(done["status"] == "COMPLETED" and done["trigger"] == "event" and done["input"] == {"customerId": "C-100"},
              f"the case ran on the mapped input: {done['status']}, {done.get('final_output')}")
        opened = [c for c in httpx.get(f"{rt.STANDIN}/_calls").json()
                  if c["environment"] == "production" and c["method"] == "POST"]
        check(len(opened) == 1, f"it opened the account in production ({len(opened)})")

        again = send(url, event)
        check(again.status_code == 200 and again.json().get("id") == execution_id,
              "the same event delivered again is the same case")
        other = send(url, {**event, "id": "evt-other", "type": "loan.withdrawn"})
        check(other.status_code == 200 and "ignored" in other.json(), f"another event type is ignored ({other.json()})")
        for label, response in (("a forged event", send(url, {**event, "id": "f"}, secret="guess")),
                                ("a replayed event", send(url, {**event, "id": "r"}, age=600)),
                                ("an unsigned event", send(url, {**event, "id": "u"}, signed=False))):
            check(response.status_code == 401, f"{label} is refused ({response.status_code}: {response.json().get('detail')})")
        executions = arch.get(f"{dep}/executions").json()
        check(len(executions) == 1, f"only the one case exists ({len(executions)})")
    finally:
        rt.cleanup(arch, state)

    failed = [what for ok, what in rt.results if not ok]
    print(f"\n{len(rt.results) - len(failed)}/{len(rt.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
