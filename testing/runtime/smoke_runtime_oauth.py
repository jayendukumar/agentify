"""Epic 39, US39.4: a live check that the tenant's systems start agents
with OAuth client credentials from the tenant's own IdP.

Same setup as smoke_runtime.py (the stack with local sign-in, an
OPENROUTER_API_KEY, Keycloak, the stand-in). Meridian's IdP is Keycloak's
realm "axyntro" (OIDC_ISSUER in docker-compose.yml), which has a service
account client "loan-system" with the audience "axyntro-runtime":

    python testing/runtime/core_banking_standin.py      # in another terminal
    python testing/runtime/smoke_runtime_oauth.py

What it does, as Meridian, on an active deployment of the loan onboarding
agent (built the way smoke_runtime.py builds it):

1. Registers "loan-system" as a runtime client: no token is issued here.
2. The loan system gets an access token from Keycloak (client
   credentials) and starts a case with it; the case completes and is
   read back with the same kind of token.
3. Refused with 401: a token from another realm (the core-banking realm's
   agent client), a token whose signature was tampered with, and no token.
4. Once the client is revoked, a fresh Keycloak token is refused too.

Cleans up the process (and with it the deployment) afterwards.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_runtime as rt  # noqa: E402

KEYCLOAK = "http://127.0.0.1:8180"
TERMINAL = ("COMPLETED", "FAILED", "ESCALATED", "KILLED", "TIMED_OUT")


def client_credentials(realm: str, client_id: str, secret: str) -> str:
    response = httpx.post(f"{KEYCLOAK}/realms/{realm}/protocol/openid-connect/token",
                          data={"grant_type": "client_credentials"}, auth=(client_id, secret), timeout=10)
    if response.status_code != 200:
        sys.exit(f"Keycloak refused {client_id} in realm {realm} ({response.status_code}: {response.text[:200]}). "
                 "Is the client in testing/keycloak/axyntro-realm.json imported?")
    return response.json()["access_token"]


def loan_system_token() -> str:
    return client_credentials("axyntro", "loan-system", "loan-system-dev-only-secret")


def caller(token: str) -> httpx.Client:
    return httpx.Client(base_url=rt.API, timeout=60,
                        headers={"Host": "localhost:8000", "Authorization": f"Bearer {token}"})


def main() -> None:
    rt.preflight()
    loan_system_token()  # fail early if Keycloak lacks the client
    httpx.post(f"{rt.STANDIN}/_reset")
    arch = rt.session("rt-architect", ["architect", "analyst", "admin", "process_owner"])
    appr = rt.session("rt-approver", ["approver", "process_owner"])
    state: dict = {}
    client_id = None
    check = rt.check
    try:
        process_id, _, deployment, _ = rt.prepare(arch, appr, state, name="Smoke: OAuth runtime client")
        start_url = f"/runtime/v1/deployments/{deployment['id']}/executions"

        # ---- 1. register the loan system as an OAuth client -----------------------------
        created = arch.post("/api/runtime/clients", json={"label": "Smoke loan system (OAuth)",
                                                           "oauth_client_id": "loan-system",
                                                           "deployment_ids": [deployment["id"]]})
        check(created.status_code == 201, f"loan-system registered as an OAuth client ({created.status_code})")
        created = created.json()
        client_id = created["id"]
        check(created["token"] is None and created["oauth_audience"] == "axyntro-runtime",
              f"no token issued here; the audience expected is {created['oauth_audience']!r}")

        # ---- 2. a case started with a Keycloak access token --------------------------------
        with caller(loan_system_token()) as loan:
            started = loan.post(start_url, json={"input": {"customerId": "C-100"}},
                                headers={"Idempotency-Key": "oauth-1"})
            check(started.status_code == 202, f"a client-credentials token started a case ({started.status_code}: "
                                              f"{started.text[:200]})")
            done = rt.wait_for(lambda: loan.get(f"/runtime/v1/executions/{started.json()['id']}").json(),
                               lambda e: e["status"] in TERMINAL)
            check(done["status"] == "COMPLETED", f"the case completed: {done.get('final_output')}")
        detail = arch.get(f"/api/processes/{process_id}/executions/{started.json()['id']}").json()
        check(detail.get("client_id") == client_id, f"the execution records the client ({detail.get('client_id')})")

        # ---- 3. tokens that don't prove the client --------------------------------------
        other_realm = client_credentials("core-banking", "agent-loans", "agent-loans-dev-only-secret")
        header, payload, signature = loan_system_token().split(".")
        tampered = ".".join([header, payload, signature[:-4] + ("AAAA" if not signature.endswith("AAAA") else "BBBB")])
        for label, token in (("a token from another realm", other_realm), ("a tampered token", tampered)):
            with caller(token) as c:
                refused = c.post(start_url, json={"input": {"customerId": "C-100"}}, headers={"Idempotency-Key": label})
            check(refused.status_code == 401, f"{label} gets 401 ({refused.json().get('detail')})")
        anonymous = httpx.post(f"{rt.API}{start_url}", json={"input": {}}, headers={"Host": "localhost:8000"})
        check(anonymous.status_code == 401, f"no token gets 401 ({anonymous.status_code})")

        # ---- 4. revoked -----------------------------------------------------------------
        arch.post(f"/api/runtime/clients/{client_id}/revoke")
        with caller(loan_system_token()) as loan:
            revoked = loan.post(start_url, json={"input": {"customerId": "C-100"}}, headers={"Idempotency-Key": "oauth-2"})
        check(revoked.status_code == 401, f"after revoking, a fresh Keycloak token gets 401 ({revoked.json().get('detail')})")
        client_id = None
    finally:
        if client_id:
            arch.post(f"/api/runtime/clients/{client_id}/revoke")
        rt.cleanup(arch, state)

    failed = [what for ok, what in rt.results if not ok]
    print(f"\n{len(rt.results) - len(failed)}/{len(rt.results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
