"""Epic 39, US39.4: a tenant's systems sign in to the runtime as OAuth
clients of the tenant's own IdP (client credentials), not only with a
bearer token issued here.

The fake IdP from test_oidc.py signs real RS256 tokens and serves its
discovery and keys, so the check runs as it would against Keycloak."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import oidc
from app.main import app
from app.tenancy.identity import IdentityConfig

from .test_oidc import ISSUER, FakeIdp
from .test_runtime import FakeSystems, _isolate, _live, _start, systems  # noqa: F401 (fixtures)

AUDIENCE = "axyntro-runtime"


@pytest.fixture
def idp():
    fake = FakeIdp()
    config = IdentityConfig(auth_mode="oidc", source="tenant", issuer=ISSUER, client_id="axyntro")
    provider = oidc.OidcProvider(config, http=httpx.Client(transport=httpx.MockTransport(fake.handle)))
    app.dependency_overrides[oidc.get_oidc_provider] = lambda: provider
    yield fake
    app.dependency_overrides.pop(oidc.get_oidc_provider, None)


def _access_token(idp: FakeIdp, client_id: str = "loan-system", **overrides) -> str:
    """What Keycloak issues for grant_type=client_credentials."""
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": [AUDIENCE, "account"], "azp": client_id, "typ": "Bearer",
              "sub": f"service-account-{client_id}", "iat": now, "exp": now + 300}
    claims.update(overrides)
    return idp.sign({k: v for k, v in claims.items() if v is not None})


def _register(client, **body) -> dict:
    created = client.post("/api/runtime/clients", json={"label": "Loan system", "oauth_client_id": "loan-system",
                                                         **body})
    assert created.status_code == 201, created.text
    return created.json()


@pytest.mark.usefixtures("systems")
def test_a_system_signs_in_with_a_client_credentials_token(client, fake_llm, idp):
    _, deployment, _ = _live(client, fake_llm)
    registered = _register(client)
    assert registered["token"] is None and registered["signing_secret"].startswith("axs_")
    assert registered["oauth_client_id"] == "loan-system" and registered["oauth_audience"] == AUDIENCE

    started = _start(_access_token(idp), deployment["id"], {})
    assert started.status_code == 202, started.text
    execution = started.json()["id"]
    read = TestClient(app).get(f"/runtime/v1/executions/{execution}",
                               headers={"Authorization": f"Bearer {_access_token(idp)}"})
    assert read.status_code == 200
    listed = {c["id"]: c for c in client.get("/api/runtime/clients").json()}
    assert listed[registered["id"]]["last_used_at"] is not None


@pytest.mark.usefixtures("systems")
def test_tokens_that_dont_prove_a_registered_client_get_401(client, fake_llm, idp):
    _, deployment, bearer = _live(client, fake_llm)
    _register(client)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    refused = {
        "unregistered client": _access_token(idp, client_id="crm"),
        "the sign-in client's audience": _access_token(idp, aud="axyntro"),
        "expired": _access_token(idp, iat=now - 900, exp=now - 600),
        "another issuer": _access_token(idp, iss="https://evil.test/realms/axyntro"),
        "an ID token": _access_token(idp, typ="ID", nonce="n"),
        "no client named": _access_token(idp, azp=None),
        "forged with another key": jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "azp": "loan-system",
                                               "exp": now + 300}, other_key, algorithm="RS256",
                                              headers={"kid": idp.kid}),
        "HMAC signed": jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "azp": "loan-system", "exp": now + 300},
                                  "client-secret", algorithm="HS256"),
        "not a token": "garbage",
    }
    for case, token in refused.items():
        response = _start(token, deployment["id"], {}, key=case)
        assert response.status_code == 401, (case, response.text)
        assert 'error="invalid_token"' in response.headers["www-authenticate"], case
    # The bearer token still works alongside.
    assert _start(bearer, deployment["id"], {}).status_code == 202


@pytest.mark.usefixtures("systems")
def test_a_revoked_oauth_client_is_refused_and_can_be_registered_again(client, fake_llm, idp):
    _, deployment, _ = _live(client, fake_llm)
    first = _register(client)
    duplicate = client.post("/api/runtime/clients", json={"label": "Again", "oauth_client_id": "loan-system"})
    assert duplicate.status_code == 409
    assert client.post(f"/api/runtime/clients/{first['id']}/revoke").status_code == 200
    assert _start(_access_token(idp), deployment["id"], {}).status_code == 401
    _register(client, oauth_audience="api://axyntro-runtime")
    assert _start(_access_token(idp), deployment["id"], {}, key="k2").status_code == 401  # the old audience
    token = _access_token(idp, aud="api://axyntro-runtime")
    assert _start(token, deployment["id"], {}, key="k3").status_code == 202


@pytest.mark.usefixtures("systems")
def test_an_oauth_client_limited_to_other_deployments_gets_404(client, fake_llm, idp):
    _, deployment, _ = _live(client, fake_llm)
    _register(client, deployment_ids=[])
    assert _start(_access_token(idp), deployment["id"], {}).status_code == 404


def test_an_idp_that_cant_be_reached_gets_503(client, idp):
    _register(client)
    token = _access_token(idp)
    idp.down = True
    oidc_provider = app.dependency_overrides[oidc.get_oidc_provider]()
    oidc_provider._jwks, oidc_provider._discovery = [], None
    response = TestClient(app).post("/runtime/v1/deployments/dep_x/executions", json={"input": {}},
                                    headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k"})
    assert response.status_code == 503


def test_without_an_idp_only_a_bearer_token_client_can_be_registered(client):
    refused = client.post("/api/runtime/clients", json={"label": "Loan system", "oauth_client_id": "loan-system"})
    assert refused.status_code == 422
    assert "no identity provider" in refused.json()["detail"]
    assert client.post("/api/runtime/clients", json={"label": "x", "oauth_audience": "a"}).status_code == 422
    assert client.post("/api/runtime/clients", json={"label": "Loan system"}).json()["token"].startswith("axr_")
