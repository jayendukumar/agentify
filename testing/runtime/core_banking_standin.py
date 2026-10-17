"""Epic 39 live test: a stand-in for a customer's production core-banking
system, run on the host at :9100.

- `/sandbox/...`: the test copy the digital twin calls in API mode (Epic
  29). No authentication, like most sandboxes.
- `/customers/{id}`, `/accounts`: "production". Every call needs a bearer
  token from the `core-banking` Keycloak realm, issued to `agent-loans`
  by client credentials, carrying `core.read` (lookups) or `core.write`
  (opening an account). Writes need an Idempotency-Key, and a repeated
  key returns the first answer instead of opening a second account.
- `/openapi-spec.json`: the spec the catalog imports.
- `/_calls`: what production received, for the smoke test to check.
- `/_slow`: POST {"seconds": n} makes production lookups take n seconds,
  so the smoke test can kill an agent while a call is in flight.
- `/_fail`: POST {"on": true} makes production lookups answer 503, so the
  smoke test can open the agent's circuit breaker.
- `/hooks/agent-done`: not core banking's, but the loan system's callback
  endpoint (US39.4), hosted here so the smoke test needs one service.
  Records the raw body and signature; `/_hooks` lists them, and
  `/_hooks_fail` {"times": n} makes the next n answer 503.
- `/v1/traces`, `/v1/metrics`: an OpenTelemetry collector (OTLP/HTTP
  JSON, US39.14), and `/hooks/alert`: the operations team's alert
  webhook. `/_otel` and `/_alerts` list what they received.

    python testing/runtime/core_banking_standin.py
"""

from __future__ import annotations

import time
from typing import Any

import jwt
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request

ISSUER = "http://localhost:8180/realms/core-banking"
JWKS = jwt.PyJWKClient(f"{ISSUER}/protocol/openid-connect/certs")
CLIENT_ID = "agent-loans"

app = FastAPI(title="Core Banking stand-in")
calls: list[dict[str, Any]] = []
accounts: dict[str, dict[str, Any]] = {}
slow = {"seconds": 0.0}
failing = {"on": False}
hooks: list[dict[str, Any]] = []
hooks_failing = {"times": 0}
otel: list[dict[str, Any]] = []
alerts: list[dict[str, Any]] = []

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Core Banking API", "version": "1.0"},
    "paths": {
        "/customers/{customerId}": {
            "get": {
                "operationId": "getCustomer",
                "summary": "Fetch a customer and their risk rating",
                "parameters": [{"name": "customerId", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                    "id": {"type": "string"}, "name": {"type": "string"}, "riskRating": {"type": "string"}}}}}}},
            }
        },
        "/accounts": {
            "post": {
                "operationId": "openAccount",
                "summary": "Open an account for a customer",
                "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                    "customerId": {"type": "string"}, "product": {"type": "string"}}}}}},
                "responses": {"201": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                    "accountId": {"type": "string"}}}}}}},
            }
        },
    },
}

CUSTOMERS = {
    "C-100": {"id": "C-100", "name": "Ada Lovelace", "riskRating": "low"},
    "C-200": {"id": "C-200", "name": "Charles Babbage", "riskRating": "high"},
}


def _check(authorization: str | None, scope: str) -> dict[str, Any]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "A bearer token is required")
    try:
        key = JWKS.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], issuer=ISSUER, options={"verify_aud": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid token: {exc}") from exc
    if claims.get("azp") != CLIENT_ID:
        raise HTTPException(403, f"Token was issued to {claims.get('azp')}, not {CLIENT_ID}")
    if scope not in (claims.get("scope") or "").split():
        raise HTTPException(403, f"Token lacks scope {scope} (has: {claims.get('scope')})")
    return claims


def _record(request: Request, environment: str, claims: dict[str, Any] | None) -> None:
    calls.append({
        "at": time.time(), "environment": environment, "method": request.method, "path": request.url.path,
        "client": (claims or {}).get("azp"), "scope": (claims or {}).get("scope"),
        "idempotency_key": request.headers.get("idempotency-key"),
    })


@app.get("/openapi-spec.json")
def spec() -> dict[str, Any]:
    return SPEC


@app.get("/sandbox/customers/{customer_id}")
def sandbox_customer(customer_id: str, request: Request) -> dict[str, Any]:
    _record(request, "sandbox", None)
    return CUSTOMERS.get(customer_id) or {"error": "not found"}


@app.post("/sandbox/accounts", status_code=201)
def sandbox_account(body: dict[str, Any], request: Request) -> dict[str, Any]:
    _record(request, "sandbox", None)
    return {"accountId": f"SBX-{body.get('customerId')}"}


@app.get("/customers/{customer_id}")
def customer(customer_id: str, request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    claims = _check(authorization, "core.read")
    _record(request, "production", claims)
    if failing["on"]:
        raise HTTPException(503, "Core banking is down for maintenance")
    if slow["seconds"]:
        time.sleep(slow["seconds"])
    found = CUSTOMERS.get(customer_id)
    if found is None:
        raise HTTPException(404, "No such customer")
    return found


@app.post("/accounts", status_code=201)
def open_account(body: dict[str, Any], request: Request, authorization: str | None = Header(default=None),
                 idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    claims = _check(authorization, "core.write")
    _record(request, "production", claims)
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    if idempotency_key not in accounts:
        accounts[idempotency_key] = {"accountId": f"ACC-{len(accounts) + 1:04d}", "customerId": body.get("customerId")}
    return accounts[idempotency_key]


@app.post("/hooks/agent-done")
async def agent_done(request: Request) -> dict[str, bool]:
    hooks.append({"at": time.time(), "body": (await request.body()).decode(),
                  "signature": request.headers.get("axyntro-signature")})
    if hooks_failing["times"] > 0:
        hooks_failing["times"] -= 1
        raise HTTPException(503, "The loan system is restarting")
    return {"ok": True}


@app.post("/v1/traces")
@app.post("/v1/metrics")
async def otlp(request: Request) -> dict[str, Any]:
    otel.append({"path": request.url.path, "body": (await request.body()).decode(),
                 "headers": dict(request.headers)})
    return {"partialSuccess": {}}


@app.post("/hooks/alert")
async def alert(request: Request) -> dict[str, bool]:
    alerts.append(await request.json())
    return {"ok": True}


@app.get("/_otel")
def recorded_otel() -> list[dict[str, Any]]:
    return otel


@app.get("/_alerts")
def recorded_alerts() -> list[dict[str, Any]]:
    return alerts


@app.get("/_hooks")
def recorded_hooks() -> list[dict[str, Any]]:
    return hooks


@app.post("/_hooks_fail")
def set_hooks_failing(body: dict[str, int]) -> dict[str, int]:
    hooks_failing["times"] = int(body.get("times", 0))
    return hooks_failing


@app.get("/_calls")
def recorded() -> list[dict[str, Any]]:
    return calls


@app.post("/_slow")
def set_slow(body: dict[str, float]) -> dict[str, float]:
    slow["seconds"] = float(body.get("seconds", 0))
    return slow


@app.post("/_fail")
def set_failing(body: dict[str, bool]) -> dict[str, bool]:
    failing["on"] = bool(body.get("on"))
    return failing


@app.post("/_reset")
def reset() -> dict[str, bool]:
    calls.clear()
    accounts.clear()
    slow["seconds"] = 0.0
    failing["on"] = False
    hooks.clear()
    hooks_failing["times"] = 0
    otel.clear()
    alerts.clear()
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9100, log_level="warning")
