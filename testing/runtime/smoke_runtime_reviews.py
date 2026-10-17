"""Epic 39, build step 3: a live check of the review inbox, the circuit
breaker and pausing, end to end. Same set-up as smoke_runtime.py (the
stack with local sign-in, Keycloak's core-banking realm, the stand-in,
a real model); the agent here asks for a review before it acts.

    python testing/runtime/smoke_runtime_reviews.py

1. The case waits in the inbox, for the matrix role that may approve its
   amount. The person the case is for can't decide it; an approver does,
   and the case carries on to open the account.
2. Production starts failing: the agent's circuit opens, and the next
   case is handed to a person instead of calling the system. A reviewer
   resolves it by hand.
3. Paused with new work queued: a case waits, and runs once resumed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

import smoke_runtime as base  # noqa: E402

API, STANDIN = base.API, base.STANDIN
check, session, wait_for = base.check, base.session, base.wait_for
DONE = ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT")


def case(caller: httpx.Client, deployment_id: str, key: str, customer: str, requested_by: str | None = None) -> str:
    started = caller.post(f"/runtime/v1/deployments/{deployment_id}/executions",
                          json={"input": {"customerId": customer}, "requested_by": requested_by},
                          headers={"Idempotency-Key": key})
    if started.status_code not in (200, 202):
        sys.exit(f"Couldn't start case {key}: {started.status_code} {started.text[:300]}")
    return started.json()["id"]


def drive(arch: httpx.Client, appr: httpx.Client, process_id: str, execution_id: str, *, approvals: int = 4) -> tuple[dict, int]:
    """Waits for the case to end, approving each review it asks for (the
    model may ask more than once). Returns the case and how many it asked."""
    asked = 0
    while True:
        e = wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{execution_id}").json(),
                     lambda e: e["status"] in ("WAITING_FOR_HUMAN", *DONE))
        if e["status"] != "WAITING_FOR_HUMAN" or asked >= approvals:
            return e, asked
        task = wait_for(lambda: [t for t in appr.get("/api/runtime/inbox").json() if t["execution_id"] == execution_id],
                        bool, timeout=30)
        if not task:
            return e, asked
        asked += 1
        appr.post(f"/api/processes/{process_id}/review-tasks/{task[0]['id']}/decide",
                  json={"decision": "approve", "comment": "Low risk, go ahead"})
        wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{execution_id}").json(),
                 lambda e: e["status"] != "WAITING_FOR_HUMAN", timeout=60)


def main() -> None:
    base.preflight()
    httpx.post(f"{STANDIN}/_reset")
    arch = session("rt-architect", ["architect", "analyst", "admin", "process_owner"])
    appr = session("rt-approver", ["approver", "process_owner"])
    lee = session("Lee Officer", ["approver"])
    state: dict = {}
    try:
        authorities = [arch.post("/api/organization/entities", json={
            "kind": "approval_authority", "name": f"{role}: account opening",
            "data": {"role_name": role, "decision_type": "account opening", "limit_amount": limit, "currency": "EUR"},
        }).json() for role, limit in (("Branch Lead", None),)]
        state["authorities"] = authorities
        process_id, _, deployment, dep = base.prepare(arch, appr, state, checkpoint="review_before_action",
                                                      name="Smoke: loan onboarding with review")
        token = arch.post("/api/runtime/clients", json={"label": "Smoke loan system"}).json()["token"]
        caller = httpx.Client(base_url=API, headers={"Host": "localhost:8000", "Authorization": f"Bearer {token}"},
                              timeout=60)
        detail = arch.get(dep).json()
        check(detail["limits"].get("executions_per_minute", 0) >= 10 and detail["limits"].get("basis"),
              f"caps were set from the twin's evidence ({detail['limits'].get('basis')})")

        # ---- 1. the inbox --------------------------------------------------------------------
        first = case(caller, deployment["id"], "review-1", "C-100", requested_by="Lee Officer")
        waiting = wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{first}").json(),
                           lambda e: e["status"] in ("WAITING_FOR_HUMAN", *DONE))
        check(waiting["status"] == "WAITING_FOR_HUMAN", f"the agent stopped to ask for a review ({waiting['status']})")
        writes_before = [c for c in httpx.get(f"{STANDIN}/_calls").json() if c["method"] == "POST"
                         and c["environment"] == "production"]
        check(not writes_before, "nothing was written to production before the review")
        inbox = [t for t in appr.get("/api/runtime/inbox").json() if t["execution_id"] == first]
        check(len(inbox) == 1 and inbox[0]["kind"] == "checkpoint",
              f"the approver's inbox has it: '{inbox[0]['summary'] if inbox else None}' for "
              f"{inbox[0]['assignee_role'] if inbox else None}")
        task_url = f"/api/processes/{process_id}/review-tasks/{inbox[0]['id']}"
        refused = lee.post(f"{task_url}/decide", json={"decision": "approve"})
        check(refused.status_code == 403, f"the person the case is for can't decide it ({refused.json().get('detail')})")
        decided = appr.post(f"{task_url}/decide", json={"decision": "approve", "comment": "Low risk, go ahead"})
        check(decided.status_code == 200, "the approver approved it")
        wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{first}").json(),
                 lambda e: e["status"] != "WAITING_FOR_HUMAN", timeout=60)
        done, more = drive(arch, appr, process_id, first)
        approval = next((s for s in done["steps"] if s["type"] == "HUMAN_APPROVED"), {})
        check(done["status"] == "COMPLETED" and approval.get("reason") == "Approved by rt-approver: Low risk, go ahead",
              f"the case carried on and completed after {1 + more} review(s) ({done['status']}, "
              f"{done.get('final_output')}; steps {[s['type'] for s in done['steps']][-8:]})")
        writes = [c for c in httpx.get(f"{STANDIN}/_calls").json() if c["method"] == "POST" and c["environment"] == "production"]
        check(len(writes) == 1, f"the account was opened after the approval ({len(writes)} write)")

        # ---- 2. the circuit breaker ------------------------------------------------------------
        arch.put(f"{dep}/settings", json={"pause_rules": {
            "circuit": {"failures_to_open": 2, "window": 20, "min_calls": 10, "max_error_rate": 0.5, "cooldown_seconds": 300},
            "errors": {"enabled": False}}})
        httpx.post(f"{STANDIN}/_fail", json={"on": True})
        for i in range(3):
            if (arch.get(dep).json()["circuits"] or [{}])[0].get("state") == "open":
                break
            failing_case = case(caller, deployment["id"], f"down-{i}", "C-100")
            drive(arch, appr, process_id, failing_case)
        circuit = (arch.get(dep).json()["circuits"] or [{}])[0]
        check(circuit.get("state") == "open", f"production's failures opened the circuit ({circuit.get('reason')})")
        calls_before = len(httpx.get(f"{STANDIN}/_calls").json())
        blocked = case(caller, deployment["id"], "blocked", "C-100")
        ended, _ = drive(arch, appr, process_id, blocked)
        check(ended["status"] == "ESCALATED" and len(httpx.get(f"{STANDIN}/_calls").json()) == calls_before,
              f"the next case went to a person without calling production ({ended['status']})")
        escalation = [t for t in appr.get("/api/runtime/inbox").json() if t["execution_id"] == blocked]
        resolved = appr.post(f"/api/processes/{process_id}/review-tasks/{escalation[0]['id']}/resolve",
                             json={"outcome": {"decision": "approve", "accountId": "MANUAL-1"},
                                   "comment": "Opened by hand in the branch"}) if escalation else None
        check(resolved is not None and resolved.status_code == 200, "a reviewer resolved it by hand")
        httpx.post(f"{STANDIN}/_fail", json={"on": False})

        # ---- 3. pause, queue, resume -------------------------------------------------------------
        arch.put(f"{dep}/settings", json={"when_paused": "queue"})
        paused = arch.post(f"{dep}/pause", json={"reason": "Smoke test: month-end freeze"})
        check(paused.status_code == 200 and paused.json()["status"] == "paused", "paused by hand")
        held = case(caller, deployment["id"], "held", "C-100")
        time.sleep(5)
        status = arch.get(f"/api/processes/{process_id}/executions/{held}").json()["status"]
        check(status == "QUEUED", f"while paused, a new case waits ({status})")
        resumed = arch.post(f"{dep}/resume", json={"comment": "Freeze over"})
        check(resumed.status_code == 200 and resumed.json()["status"] == "active", "resumed, with a comment")
        after = wait_for(lambda: arch.get(f"/api/processes/{process_id}/executions/{held}").json(),
                         lambda e: e["status"] != "QUEUED", timeout=120)
        check(after["status"] in ("RUNNING", "WAITING_FOR_MODEL", "WAITING_FOR_TOOL", "WAITING_FOR_HUMAN", *DONE),
              f"and the held case ran ({after['status']})")
        caller.close()
    finally:
        base.cleanup(arch, state)
        for authority in state.get("authorities", []):
            base.subprocess.run(["docker", "exec", "axyntro-db-1", "psql", "-U", "agentic", "-d",
                                 "agentic_solution_generator", "-qc",
                                 f"DELETE FROM context_entities WHERE id = '{authority['id']}'"], check=False)

    failed = [what for ok, what in base.results if not ok]
    print(f"\n{len(base.results) - len(failed)}/{len(base.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
