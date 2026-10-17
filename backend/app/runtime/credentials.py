"""Epic 39, US39.7: how a production adapter authenticates to a system.

A binding's `credential` holds references only:

- `{"kind": "oauth_client_credentials", "token_url": ..., "client_id": ...,
  "secret_ref": "secret://tenant/<name>", "scopes": {"read": [...],
  "update": [...]}}`: a short-lived token from the system's
  authorization server, asked for with only the scopes mapped to the
  calling contract's action. A read contract's token can't be used for
  a mutating call.
- `{"kind": "api_key", "header": "X-API-Key", "secret_ref": ...}`: for a
  system with nothing better.

Tokens are cached in this process's memory until shortly before they
expire, and are never stored, logged or shown to the model. They're
attached by the adapter after the Tool Gateway allowed the call.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app import secret_store
from app.tenancy.identity import current_slug

KINDS = ("oauth_client_credentials", "api_key")
_EXPIRY_MARGIN_SECONDS = 30

# (cache scope, client id, scopes) -> (token, monotonic expiry)
_tokens: dict[tuple[str, str, tuple[str, ...]], tuple[str, float]] = {}


class CredentialError(Exception):
    """The credential is unusable. The message never contains a secret."""


def problems(credential: dict[str, Any]) -> list[str]:
    """What's wrong with a credential's shape and references, without
    resolving anything."""
    kind = credential.get("kind")
    if kind not in KINDS:
        return ["no credential is set" if not kind else f"unknown credential kind '{kind}'"]
    found = []
    ref = credential.get("secret_ref") or ""
    try:
        secret_store.check_ref(ref, current_slug() or "", example="secret://tenant/erp_client_secret")
    except secret_store.SecretRefError as exc:
        found.append(str(exc))
    if kind == "oauth_client_credentials":
        found += _oauth_problems(credential)
    return found


def _oauth_problems(credential: dict[str, Any]) -> list[str]:
    found = [f"the {label} is missing" for key, label in (("token_url", "token URL"), ("client_id", "client id"))
             if not credential.get(key)]
    scopes = credential.get("scopes") or {}
    if not isinstance(scopes, dict) or not all(isinstance(v, list) for v in scopes.values()):
        found.append("scopes map an action to a list of scopes, like {\"read\": [\"erp.read\"]}")
    return found


def _secret(credential: dict[str, Any]) -> str:
    ref = credential.get("secret_ref") or ""
    slug = current_slug()
    value = secret_store.resolve(ref, slug)
    if not value:
        raise CredentialError(f"the secret isn't set: the hosting company stores it as "
                              f"{secret_store.where_to_put(ref, slug or '')}")
    return value


async def _fetch_token(credential: dict[str, Any], scopes: tuple[str, ...]) -> tuple[str, float]:
    from app.runtime.adapters import UnsafeProductionEndpoint, check_production_url

    token_url = credential["token_url"]
    try:
        check_production_url(token_url)
    except UnsafeProductionEndpoint as exc:
        raise CredentialError(f"the token URL is refused: {exc}") from exc
    data = {"grant_type": "client_credentials", "client_id": credential["client_id"],
            "client_secret": _secret(credential)}
    if scopes:
        data["scope"] = " ".join(scopes)
    if credential.get("audience"):
        data["audience"] = credential["audience"]
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(token_url, data=data)
    except httpx.HTTPError as exc:
        raise CredentialError(f"the token request failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise CredentialError(f"the authorization server refused the token request ({response.status_code})")
    try:
        body = response.json()
        token = str(body["access_token"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CredentialError("the authorization server's reply had no access_token") from exc
    expires_in = float(body.get("expires_in") or 300)
    return token, time.monotonic() + max(expires_in - _EXPIRY_MARGIN_SECONDS, 0)


async def headers_for(credential: dict[str, Any], *, scope_key: str, action: str) -> dict[str, str]:
    """The authentication headers for one call. `scope_key` names whose
    cache entry this is (the binding), so revoking an identity can drop it."""
    found = problems(credential)
    if found:
        raise CredentialError("; ".join(found))
    if credential["kind"] == "api_key":
        return {credential.get("header") or "X-API-Key": _secret(credential)}
    scopes = tuple(sorted((credential.get("scopes") or {}).get(action, [])))
    key = (scope_key, credential["client_id"], scopes)
    cached = _tokens.get(key)
    if cached is None or cached[1] <= time.monotonic():
        cached = await _fetch_token(credential, scopes)
        _tokens[key] = cached
    return {"Authorization": f"Bearer {cached[0]}"}


def forget(scope_keys: set[str]) -> None:
    """Drops cached tokens, e.g. when an identity is revoked."""
    for key in [k for k in _tokens if k[0] in scope_keys]:
        del _tokens[key]
