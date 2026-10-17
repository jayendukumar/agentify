"""Epic 39, US39.18: a live check of a certified hand-off, from the point of
view of the customer's own agent platform.

Same setup as smoke_runtime.py (the stack with local sign-in, an
OPENROUTER_API_KEY, Keycloak, the stand-in).

    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime_handoff.py

1. v1 of the loan onboarding agent is active (as smoke_runtime.py builds
   it); v2 is approved and handed off instead of activated.
2. As the receiving platform, with nothing but HTTP and its own code: fetch
   the bundle and the public key (JWKS), check the evidence signature
   (EdDSA), recompute the content hash, and see that a bundle changed in
   transit fails.
3. The platform reports three executions back, signed in as an OAuth
   client of Meridian's Keycloak (US39.4); a repeat is skipped. The
   Runtime overview shows them on the external deployment.

Cleans up the process (and with it the deployments) afterwards.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import jwt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_runtime as rt  # noqa: E402
import smoke_runtime_oauth as oauth  # noqa: E402

CONTENT = ("agent_card", "mcp_tools", "definition", "tool_contracts", "guardrails", "snapshot")


def platform_verifies(bundle: dict, jwks: dict) -> dict:
    """What the receiving platform does, independent of Axyntro's code."""
    kid = jwt.get_unverified_header(bundle["evidence_jws"])["kid"]
    key = jwt.PyJWK(next(k for k in jwks["keys"] if k["kid"] == kid)).key
    claims = jwt.decode(bundle["evidence_jws"], key, algorithms=["EdDSA"])
    content = {k: bundle[k] for k in CONTENT}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    if digest != claims["content_sha256"]:
        raise ValueError("content hash mismatch")
    return claims


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
        process_id, _, _, _ = rt.prepare(arch, appr, state, name="Smoke: hand-off")
        base = f"/api/processes/{process_id}"
        artifact2 = arch.post(f"{base}/blueprint/nodes/Task_a/agent-artifact").json()
        appr.post(f"{base}/blueprint/agent-artifacts/{artifact2['id']}/approve").raise_for_status()
        v2 = arch.post(f"{base}/agent-artifacts/{artifact2['id']}/deployments").json()
        dep2 = f"{base}/deployments/{v2['id']}"

        own = arch.post(f"{dep2}/handoff")
        check(own.status_code == 403, "the architect who asked for v2 can't hand it off")
        handed = appr.post(f"{dep2}/handoff")
        check(handed.status_code == 200, f"an approver handed v2 off ({handed.status_code} {handed.text[:120]})")

        # ---- as the receiving platform ------------------------------------------------
        bundle = arch.get(f"{dep2}/handoff").json()
        jwks = httpx.get(f"{rt.API}/runtime/v1/handoff/keys", headers={"Host": "localhost:8000"}).json()
        claims = platform_verifies(bundle, jwks)
        check(claims["sub"] == v2["id"] and claims["agent"]["version"] == 2,
              f"the evidence signature and content hash check out (key {jwks['keys'][0]['kid']})")
        check(bundle["mcp_tools"][0]["annotations"]["destructiveHint"] is True
              and bundle["agent_card"]["skills"][0]["name"] == "Loan Onboarding Agent",
              f"the bundle has the A2A card and MCP tool ({bundle['mcp_tools'][0]['name']})")
        tampered = json.loads(json.dumps(bundle))
        tampered["tool_contracts"][0]["action"] = "read"
        try:
            platform_verifies(tampered, jwks)
            check(False, "a changed bundle fails verification")
        except ValueError:
            check(True, "a changed bundle fails verification")

        created = arch.post("/api/runtime/clients", json={"label": "Meridian agent platform",
                                                           "oauth_client_id": "loan-system",
                                                           "deployment_ids": [v2["id"]]}).json()
        client_id = created["id"]
        platform = oauth.caller(oauth.loan_system_token())
        runs = [{"id": f"ext-{i}", "status": "COMPLETED", "started_at": "2026-10-17T10:00:00Z",
                 "completed_at": "2026-10-17T10:00:06Z", "input": {"customerId": f"C-{i}"},
                 "final_output": {"decision": "approve"}, "total_cost_usd": 0.001, "total_tokens": 700,
                 "tool_calls": [{"system": "Core Banking"}, {"system": "Core Banking"}]} for i in range(3)]
        reported = platform.post(f"/runtime/v1/deployments/{v2['id']}/reports", json={"executions": runs})
        check(reported.status_code == 200 and reported.json() == {"accepted": 3, "duplicates": 0},
              f"the platform reported three executions ({reported.status_code} {reported.text[:120]})")
        again = platform.post(f"/runtime/v1/deployments/{v2['id']}/reports", json={"executions": runs[:1]})
        check(again.json() == {"accepted": 0, "duplicates": 1}, "a repeated report is skipped")
        rows = arch.get("/api/runtime/deployments?window=day").json()
        row = next((r for r in rows if r["deployment"]["id"] == v2["id"]), None)
        check(row is not None and row["deployment"]["status"] == "external" and row["metrics"]["executions"] == 3
              and row["metrics"]["tools"]["Core Banking"]["calls"] == 6,
              f"the Runtime overview shows them on the external deployment "
              f"({row and row['metrics']['executions']} cases)")
        platform.close()
    finally:
        if client_id:
            arch.post(f"/api/runtime/clients/{client_id}/revoke")
        rt.cleanup(arch, state)

    failed = [what for ok, what in rt.results if not ok]
    print(f"\n{len(rt.results) - len(failed)}/{len(rt.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
