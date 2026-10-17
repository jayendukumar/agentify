"""Epic 39, build step 4: a live check of shadow, canary, rollback, live
metrics, traces, alerts and the drift check.

Same setup as smoke_runtime.py (the stack with local sign-in, an
OPENROUTER_API_KEY -- real model calls, a few cents -- Keycloak, the
stand-in, which also plays the OpenTelemetry collector and the alert
webhook):

    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime_rollout.py

What it does, as Meridian, with the loan onboarding agent (which opens
accounts, so it changes data):

1. v1 is active (built the way smoke_runtime.py builds it). Its traces go
   to the stand-in's collector, and an alert rule calls the stand-in's
   webhook.
2. v2 (the agent regenerated and approved) goes into shadow. Each live
   case sent to v1 is copied to v2: v2 looks the customer up for real
   and opens no account. The loan system reports the real outcome, and
   the comparison pairs the two versions.
3. v2 is promoted to a 50% canary: cases are split between the versions
   by idempotency key, and v2's cases open real accounts.
4. v2 is rolled back: new cases go to v1 only.
5. The Runtime overview and metrics count what happened; the collector got
   one trace per case with a span per step and no payloads; the webhook
   got the alert; the drift check says v1 has no twin baseline (it was
   approved without a batch).

The loan system signs in with Keycloak client credentials (US39.4).
Cleans up the process (and with it the deployments) afterwards, unless
SMOKE_KEEP=1, which leaves them for a look at the Runtime page.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_runtime as rt  # noqa: E402
import smoke_runtime_oauth as oauth  # noqa: E402

TERMINAL = ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT")
COLLECTOR = rt.STANDIN_FROM_API
WEBHOOK = f"{rt.STANDIN_FROM_API}/hooks/alert"


def production_opens() -> int:
    return sum(1 for c in httpx.get(f"{rt.STANDIN}/_calls").json()
               if c["environment"] == "production" and c["method"] == "POST")


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
        process_id, _, v1, dep1 = rt.prepare(arch, appr, state, name="Smoke: rollout")
        base = f"/api/processes/{process_id}"

        # ---- 1. watching v1 -------------------------------------------------------------
        monitored = arch.put(f"{dep1}/monitoring", json={
            "telemetry": {"otlp_endpoint": COLLECTOR},
            "alert_rules": [{"name": "any case", "metric": "executions", "window": "hour", "op": ">=", "threshold": 1,
                             "webhook": WEBHOOK}],
        })
        check(monitored.status_code == 200, f"v1 sends traces to the collector and has an alert rule "
                                            f"({monitored.status_code} {monitored.text[:160]})")
        client_id = arch.post("/api/runtime/clients", json={"label": "Smoke loan system",
                                                             "oauth_client_id": "loan-system"}).json()["id"]
        loan = oauth.caller(oauth.loan_system_token())

        # ---- 2. v2 in shadow ------------------------------------------------------------
        artifact2 = arch.post(f"{base}/blueprint/nodes/Task_a/agent-artifact").json()
        appr.post(f"{base}/blueprint/agent-artifacts/{artifact2['id']}/approve").raise_for_status()
        v2 = arch.post(f"{base}/agent-artifacts/{artifact2['id']}/deployments").json()
        dep2 = f"{base}/deployments/{v2['id']}"
        arch.put(f"{dep2}/bindings/Core Banking", json={
            "base_url": rt.STANDIN_FROM_API, "operations": ["getCustomer", "openAccount"],
            "credential": {"kind": "oauth_client_credentials", "token_url": rt.TOKEN_URL, "client_id": "agent-loans",
                           "secret_ref": "secret://tenant/core_banking_secret",
                           "scopes": {"update": ["core.read", "core.write"]}},
        }).raise_for_status()
        shadow = appr.post(f"{dep2}/activate", json={"rollout": "shadow"})
        check(shadow.status_code == 200 and shadow.json()["status"] == "shadow"
              and shadow.json()["previous_deployment_id"] == v1["id"],
              f"v2 went into shadow next to v1 without a waiver ({shadow.status_code} {shadow.text[:160]})")

        started = loan.post(f"/runtime/v1/deployments/{v1['id']}/executions", json={"input": {"customerId": "C-100"}},
                            headers={"Idempotency-Key": "roll-1"})
        check(started.status_code == 202, f"a live case was accepted ({started.status_code})")
        live = rt.wait_for(lambda: loan.get(f"/runtime/v1/executions/{started.json()['id']}").json(),
                           lambda e: e["status"] in TERMINAL)
        copies = rt.wait_for(lambda: arch.get(f"{dep2}/executions").json(),
                             lambda xs: xs and all(x["status"] in TERMINAL for x in xs))
        copy = copies[0] if copies else {}
        check(live["status"] == "COMPLETED" and copy.get("shadow_of") == started.json()["id"]
              and copy.get("status") == "COMPLETED",
              f"v1 handled the case and v2 got a copy ({live['status']}; copy {copy.get('status')})")
        check(production_opens() == 1, f"only the live case opened an account ({production_opens()} opened)")
        copy_steps = arch.get(f"{base}/executions/{copy['id']}").json()["steps"] if copy else []
        not_sent = [s for s in copy_steps if s["type"] == "TOOL_EXECUTED" and (s.get("result") or {}).get("shadow")]
        looked_up = [s for s in copy_steps if s["type"] == "TOOL_EXECUTED" and not (s.get("result") or {}).get("shadow")]
        check(bool(not_sent) and bool(looked_up),
              f"the copy looked the customer up for real and recorded the account it would open "
              f"({len(looked_up)} real, {len(not_sent)} not sent)")
        reported = loan.post(f"/runtime/v1/executions/{started.json()['id']}/outcome",
                             json={"outcome": {"decision": live["final_output"].get("decision", "approve")}})
        check(reported.status_code == 200, f"the loan system reported the real outcome ({reported.status_code})")
        comparison = arch.get(f"{dep2}/comparison").json()
        paired = comparison.get("paired") or {}
        check(paired.get("pairs") == 1 and paired.get("with_known_outcome") == 1,
              f"the comparison pairs the versions: {comparison.get('reading')}")

        # ---- 3. a 50% canary ------------------------------------------------------------
        own = arch.post(f"{dep2}/promote", json={"share": 50})
        check(own.status_code == 403, "the architect who asked for v2 can't promote it")
        canary = appr.post(f"{dep2}/promote", json={"share": 50})
        check(canary.status_code == 200 and canary.json()["traffic_share"] == 50,
              f"v2 promoted to a 50% canary ({canary.status_code})")
        keys = [f"canary-{i}" for i in range(6)]
        for key in keys:
            loan.post(f"/runtime/v1/deployments/{v1['id']}/executions", json={"input": {"customerId": "C-100"}},
                      headers={"Idempotency-Key": key})
        rt.wait_for(lambda: arch.get(f"{dep2}/executions").json() + arch.get(f"{dep1}/executions").json(),
                    lambda xs: len(xs) >= 8 and all(x["status"] in TERMINAL for x in xs))
        on_v2 = [x for x in arch.get(f"{dep2}/executions").json() if not x["shadow_of"]]
        on_v1 = [x for x in arch.get(f"{dep1}/executions").json()]
        check(on_v2 and len(on_v1) > 1 and len(on_v2) + len(on_v1) == 7,
              f"the cases were split by key: {len(on_v1) - 1} to v1, {len(on_v2)} to v2")
        check(production_opens() == 7, f"every live case opened its account, on either version ({production_opens()})")

        # ---- 4. rollback -------------------------------------------------------------------
        rolled = arch.post(f"{dep2}/rollback", json={"reason": "Smoke test rollback"})
        check(rolled.status_code == 200 and rolled.json()["status"] == "retired", f"v2 rolled back ({rolled.status_code})")
        after = loan.post(f"/runtime/v1/deployments/{v2['id']}/executions", json={"input": {"customerId": "C-100"}},
                          headers={"Idempotency-Key": "after-rollback"})
        landed = arch.get(f"{base}/executions/{after.json()['id']}").json()
        check(after.status_code == 202 and landed["deployment_id"] == v1["id"],
              "a case sent to v2's id after the rollback went to v1")
        rt.wait_for(lambda: loan.get(f"/runtime/v1/executions/{after.json()['id']}").json(),
                    lambda e: e["status"] in TERMINAL)

        # ---- 5. watching it ----------------------------------------------------------------
        rows = arch.get("/api/runtime/deployments?window=hour").json()
        mine = {r["deployment"]["id"]: r for r in rows if r["deployment"]["process_id"] == process_id}
        check(set(mine) == {v1["id"], v2["id"]} and mine[v1["id"]]["metrics"]["executions"] == len(on_v1) + 1,
              f"the Runtime overview lists both versions with their cases "
              f"({ {k[-6:]: r['metrics']['executions'] for k, r in mine.items()} })")
        metrics = arch.get(f"{dep1}/metrics?window=hour").json()
        check(metrics["tools"].get("Core Banking", {}).get("calls", 0) >= 2 * (len(on_v1) + 1)
              and metrics["latency_seconds"]["p95"],
              f"v1's metrics count its tool calls and time ({metrics['tools']}, p95 {metrics['latency_seconds']['p95']}s)")
        for execution in arch.get(f"{dep1}/executions").json():
            for step in arch.get(f"{base}/executions/{execution['id']}").json()["steps"]:
                if step["type"] == "TOOL_FAILED":
                    print(f"  info: a tool call failed on v1: {step.get('reason')} {json.dumps(step.get('arguments'))}")
        otel = rt.wait_for(lambda: httpx.get(f"{rt.STANDIN}/_otel").json(),
                           lambda xs: sum(x["path"] == "/v1/traces" for x in xs) >= len(on_v1) + 1, timeout=90)
        traces = [json.loads(x["body"]) for x in otel if x["path"] == "/v1/traces"]
        first = traces[0]["resourceSpans"][0]["scopeSpans"][0]["spans"] if traces else []
        check(len(traces) == len(on_v1) + 1 and len(first) > 3 and first[0]["name"] == "agent.execution",
              f"the collector got a trace per v1 case ({len(traces)}, {len(first)} spans in the first)")
        check(not any("C-100" in x["body"] or "ACC-" in x["body"] for x in otel),
              "no customer id or account number in what the collector received")
        alert_hits = rt.wait_for(lambda: httpx.get(f"{rt.STANDIN}/_alerts").json(), lambda xs: len(xs) >= 1, timeout=60)
        check(len(alert_hits) == 1 and alert_hits[0]["deployment_id"] == v1["id"],
              f"the alert webhook was called once, naming v1 ({len(alert_hits)}: "
              f"{alert_hits[0]['message'] if alert_hits else ''})")
        drift = arch.get(f"{dep1}/drift").json()
        check(drift["status"] == "no_baseline", f"drift: {drift.get('reading')}")
        loan.close()
    finally:
        if client_id:
            arch.post(f"/api/runtime/clients/{client_id}/revoke")
        if os.environ.get("SMOKE_KEEP") == "1":
            print(f"Kept process {state.get('process_id')} for a look at http://localhost:3000/runtime")
        else:
            rt.cleanup(arch, state)

    failed = [what for ok, what in rt.results if not ok]
    print(f"\n{len(rt.results) - len(failed)}/{len(rt.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
