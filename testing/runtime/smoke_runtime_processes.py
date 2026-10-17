"""Epic 39, US39.17: a live check of a case through the whole process.

Same setup as smoke_runtime.py (the stack with local sign-in, an
OPENROUTER_API_KEY -- real model calls, a few cents -- Keycloak, the
stand-in).

    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime_processes.py

1. The loan onboarding agent is active and asks for a review before it
   opens an account. Its process gets a step after it that no agent runs:
   start -> open the account (the agent) -> sign the contract (a person)
   -> end.
2. The loan system starts a process case (Keycloak client credentials).
   The agent's node waits for its review; an approver approves it in the
   inbox; the node opens the account; the case moves on to the person's
   step, which appears in the inbox; resolving it with the contract number
   finishes the case with both results in it.
3. Stopping the whole process kills a waiting case and the agent's
   deployment, and new cases are refused.

Cleans up the process (and with it the deployments) afterwards.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_runtime as rt  # noqa: E402
import smoke_runtime_oauth as oauth  # noqa: E402

TERMINAL = ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT")

REWRITE = r'''
import json, sys
from sqlalchemy import select, text
from app.bpmn.builder import build_bpmn_xml
from app.db.models import TenantModel
from app.db.session import get_owner_engine, get_session_factory
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef

process_id, deployment_id = sys.argv[1], sys.argv[2]
ref = SourceRef(document_id="d", location="p1", excerpt="x")
schema = ProcessSchema(process_name="Loan onboarding", elements=[
    ProcessElement(id="start", type="start_event", label="Application received", source_refs=[ref], confidence="high"),
    ProcessElement(id="a", type="task", label="Open the account", source_refs=[ref], confidence="high"),
    ProcessElement(id="b", type="task", label="Sign the contract", source_refs=[ref], confidence="high"),
    ProcessElement(id="end", type="end_event", label="Done", source_refs=[ref], confidence="high"),
], flows=[ProcessFlow(id="f1", **{"from": "start"}, to="a"), ProcessFlow(id="f2", **{"from": "a"}, to="b"),
          ProcessFlow(id="f3", **{"from": "b"}, to="end")])
xml, _ = build_bpmn_xml(process_id, schema)
with get_owner_engine().begin() as conn:
    version_id = conn.scalar(text("SELECT a.source_baseline_version_id FROM agent_artifacts a JOIN agent_deployments d "
                                  "ON d.agent_artifact_id = a.id WHERE d.id = :d"), {"d": deployment_id})
    conn.execute(text("UPDATE versions SET xml = :x WHERE id = :v"), {"x": xml, "v": version_id})
print("ok")
'''


def add_persons_step(process_id: str, deployment_id: str) -> None:
    out = subprocess.run(["docker", "exec", "-i", "axyntro-api-1", "python", "-", process_id, deployment_id],
                         input=REWRITE, capture_output=True, text=True, check=False)
    if out.stdout.strip() != "ok":
        sys.exit(f"Adding the person's step failed: {out.stderr[-600:]}")


def main() -> None:
    rt.preflight()
    oauth.loan_system_token()
    httpx.post(f"{rt.STANDIN}/_reset")
    arch = rt.session("rt-architect", ["architect", "analyst", "admin", "process_owner"])
    appr = rt.session("rt-approver", ["approver", "process_owner"])
    state: dict = {}
    client_id = None
    check = rt.check
    try:
        process_id, _, deployment, dep = rt.prepare(arch, appr, state, checkpoint="review_before_action",
                                                    name="Smoke: whole process")
        add_persons_step(process_id, deployment["id"])
        client_id = arch.post("/api/runtime/clients", json={"label": "Loan system (process)",
                                                             "oauth_client_id": "loan-system"}).json()["id"]
        loan = oauth.caller(oauth.loan_system_token())
        base = f"/api/processes/{process_id}"

        started = loan.post(f"/runtime/v1/processes/{process_id}/executions", json={"input": {"customerId": "C-100"}},
                            headers={"Idempotency-Key": "proc-1"})
        check(started.status_code == 202, f"a process case was accepted ({started.status_code} {started.text[:160]})")
        case_id = started.json()["id"]
        case = rt.wait_for(lambda: arch.get(f"{base}/process-executions/{case_id}").json(),
                           lambda c: c["status"] == "WAITING_FOR_HUMAN" and c["node_executions"])
        child = case["node_executions"][0] if case["node_executions"] else {}
        check(child.get("status") == "WAITING_FOR_HUMAN",
              f"the agent's node waits for its review ({case['status']}, node {child.get('status')})")
        # The agent may ask more than once (before the lookup, before opening
        # the account); approve each of the node's checkpoints until the case
        # moves on.
        approvals = 0
        for _ in range(4):
            inbox = rt.wait_for(lambda: appr.get("/api/runtime/inbox").json(),
                                lambda ts: any(t["execution_id"] == child.get("id") for t in ts), timeout=90)
            review = next((t for t in inbox if t["execution_id"] == child.get("id")), None)
            if review is None:
                break
            decided = appr.post(f"{base}/review-tasks/{review['id']}/decide",
                                json={"decision": "approve", "comment": "Low risk"})
            approvals += decided.status_code == 200
            case = rt.wait_for(lambda: arch.get(f"{base}/process-executions/{case_id}").json(),
                               lambda c: c["open_tasks"] or c["status"] in TERMINAL, timeout=30)
            if case["open_tasks"] or case["status"] in TERMINAL:
                break
        check(approvals >= 1, f"an approver approved the agent's actions ({approvals} review(s))")
        case = rt.wait_for(lambda: arch.get(f"{base}/process-executions/{case_id}").json(),
                           lambda c: c["open_tasks"] or c["status"] in TERMINAL)
        opened = [c for c in httpx.get(f"{rt.STANDIN}/_calls").json() if c["method"] == "POST"
                  and c["environment"] == "production"]
        check(len(opened) == 1, f"the node opened the account ({len(opened)})")
        check(case["status"] == "WAITING_FOR_HUMAN" and case["open_tasks"],
              f"the case moved on to the person's step ({case['status']}, {case['visited_path']})")
        task = next((t for t in appr.get("/api/runtime/inbox").json() if t["id"] in case["open_tasks"]), {})
        check(task.get("kind") == "human_task" and task.get("summary") == "Sign the contract",
              f"the inbox shows the person's step ({task.get('kind')}: {task.get('summary')})")
        resolved = appr.post(f"{base}/review-tasks/{task.get('id')}/resolve",
                             json={"outcome": {"decision": "approve", "contractId": "K-77"}})
        check(resolved.status_code == 200, f"the person's step was done ({resolved.status_code})")
        done = rt.wait_for(lambda: loan.get(f"/runtime/v1/process-executions/{case_id}").json(),
                           lambda c: c["status"] in TERMINAL)
        output = done.get("final_output") or {}
        check(done["status"] == "COMPLETED" and output.get("contractId") == "K-77" and output.get("accountId"),
              f"the case finished with both results ({done['status']}: accountId {output.get('accountId')}, "
              f"contractId {output.get('contractId')})")

        # ---- stopping the whole process -------------------------------------------------
        waiting = loan.post(f"/runtime/v1/processes/{process_id}/executions", json={"input": {"customerId": "C-100"}},
                            headers={"Idempotency-Key": "proc-2"}).json()["id"]
        rt.wait_for(lambda: arch.get(f"{base}/process-executions/{waiting}").json(),
                    lambda c: c["status"] == "WAITING_FOR_HUMAN")
        stopped = appr.put(f"{base}/runtime", json={"status": "killed", "reason": "Smoke test stop"})
        check(stopped.status_code == 200, f"the whole process was stopped ({stopped.status_code})")
        killed = arch.get(f"{base}/process-executions/{waiting}").json()
        check(killed["status"] == "KILLED" and arch.get(dep).json()["status"] == "killed",
              f"the waiting case and the agent's deployment were stopped ({killed['status']})")
        refused = loan.post(f"/runtime/v1/processes/{process_id}/executions", json={"input": {"customerId": "C-1"}},
                            headers={"Idempotency-Key": "proc-3"})
        check(refused.status_code == 503, f"new cases are refused ({refused.status_code}: {refused.json().get('detail')})")
        loan.close()
    finally:
        if client_id:
            arch.post(f"/api/runtime/clients/{client_id}/revoke")
        rt.cleanup(arch, state)

    failed = [what for ok, what in rt.results if not ok]
    print(f"\n{len(rt.results) - len(failed)}/{len(rt.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
