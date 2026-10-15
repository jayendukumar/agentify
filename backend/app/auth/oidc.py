"""Epic 37, US37.7-37.8: sign-in through the customer's identity provider
over OpenID Connect, with MFA verified here rather than trusted.

The API runs the whole Authorization Code flow with PKCE (a
backend-for-frontend): the browser is sent to the IdP and comes back to
`/api/auth/oidc/callback` with a one-time code, which the API exchanges
for tokens. IdP tokens never reach the browser, which only ever holds our
own session cookies (app/auth/sessions.py).

What's checked on every ID token: signature against the IdP's published
keys (JWKS), an asymmetric algorithm (never `none` or HMAC), issuer,
audience (and `azp` when there are several), expiry and issue time, and
the nonce from this sign-in. A user is matched by (issuer, `sub`) only.

Also here, for US37.12/US37.13: confirming at each session refresh that
the account is still active at the IdP, building the IdP logout URL, and
validating back-channel logout tokens.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import directory
from app.config import Settings, get_settings
from app.db.models import UserIdentityModel, UserModel
from app.ids import new_id, utcnow

logger = logging.getLogger(__name__)

# Asymmetric only: `none` means unsigned, and HMAC would make the client
# secret a signing key anyone holding it could mint tokens with.
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
CLOCK_SKEW_SECONDS = 60
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
_CACHE_SECONDS = 3600
# An unknown `kid` means the IdP rotated its keys: refetch, but not more
# often than this, so a stream of forged tokens can't hammer the IdP.
_JWKS_REFETCH_SECONDS = 30


class OidcError(Exception):
    """A refused sign-in. `code` goes to the frontend (it picks the message
    shown); the text goes to the audit log."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IdpUnavailable(Exception):
    """The IdP couldn't be reached or answered with a server error."""


class OidcProvider:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=10.0)
        self._lock = threading.Lock()
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks: list[dict[str, Any]] = []
        self._jwks_at = 0.0

    # -- discovery and keys --

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = self.http.get(url)
        except httpx.HTTPError as exc:
            raise IdpUnavailable(f"{url}: {exc}") from exc
        if response.status_code >= 500:
            raise IdpUnavailable(f"{url}: HTTP {response.status_code}")
        if response.status_code != 200:
            raise OidcError("idp_misconfigured", f"{url} returned HTTP {response.status_code}")
        return response.json()

    def discovery(self) -> dict[str, Any]:
        with self._lock:
            if self._discovery is None or time.monotonic() - self._discovery_at > _CACHE_SECONDS:
                issuer = self.settings.oidc_issuer.rstrip("/")
                url = self.settings.oidc_discovery_url or f"{issuer}/.well-known/openid-configuration"
                doc = self._get_json(url)
                # The discovery document must describe the issuer we were told
                # to trust, or every later check is against the wrong party.
                if doc.get("issuer") != self.settings.oidc_issuer:
                    raise OidcError(
                        "idp_misconfigured",
                        f"Discovery at {url} names issuer {doc.get('issuer')!r}, not OIDC_ISSUER {self.settings.oidc_issuer!r}",
                    )
                self._discovery, self._discovery_at = doc, time.monotonic()
            return self._discovery

    def _load_jwks(self, *, force: bool = False) -> None:
        with self._lock:
            age = time.monotonic() - self._jwks_at
            if self._jwks and age < _CACHE_SECONDS and not (force and age > _JWKS_REFETCH_SECONDS):
                return
        keys = self._get_json(self.discovery()["jwks_uri"]).get("keys", [])
        with self._lock:
            self._jwks = [k for k in keys if k.get("use", "sig") == "sig"]
            self._jwks_at = time.monotonic()

    def _find_key(self, kid: str | None) -> dict[str, Any] | None:
        with self._lock:
            if kid is None:  # only unambiguous when the IdP has one key
                return self._jwks[0] if len(self._jwks) == 1 else None
            return next((k for k in self._jwks if k.get("kid") == kid), None)

    def _signing_key(self, kid: str | None) -> Any:
        self._load_jwks()
        found = self._find_key(kid)
        if found is None:
            self._load_jwks(force=True)
            found = self._find_key(kid)
        if found is None:
            raise OidcError("invalid_token", f"No signing key {kid!r} in the IdP's JWKS")
        return jwt.PyJWK(found).key

    def _decode(self, token: str, *, require: list[str]) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcError("invalid_token", f"Malformed token: {exc}") from exc
        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise OidcError("invalid_token", f"Token signed with {algorithm!r}, which isn't accepted")
        key = self._signing_key(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.settings.oidc_client_id,
                issuer=self.settings.oidc_issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": require},
            )
        except jwt.PyJWTError as exc:
            raise OidcError("invalid_token", f"Token rejected: {exc}") from exc
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != self.settings.oidc_client_id:
            raise OidcError("invalid_token", "Token has several audiences and wasn't issued to this client (azp)")
        return claims

    # -- the sign-in --

    def authorization_url(self, *, state: str, nonce: str, code_verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        params = {
            "response_type": "code",
            "client_id": self.settings.oidc_client_id,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "scope": self.settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self.settings.oidc_acr_values:
            params["acr_values"] = self.settings.oidc_acr_values
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params)}"

    def _token_request(self, data: dict[str, str]) -> httpx.Response:
        try:
            return self.http.post(
                self.discovery()["token_endpoint"],
                data=data,
                auth=(self.settings.oidc_client_id, self.settings.oidc_client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise IdpUnavailable(str(exc)) from exc

    def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        response = self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.oidc_redirect_uri,
            "code_verifier": code_verifier,
        })
        if response.status_code != 200:
            raise OidcError("token_exchange_failed", f"Token endpoint returned HTTP {response.status_code}: {response.text[:200]}")
        tokens = response.json()
        if "id_token" not in tokens:
            raise OidcError("token_exchange_failed", "Token endpoint returned no ID token (is the 'openid' scope requested?)")
        return tokens

    def validate_id_token(self, token: str, *, nonce: str) -> dict[str, Any]:
        claims = self._decode(token, require=["iss", "sub", "aud", "exp", "iat"])
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("invalid_token", "ID token nonce doesn't match this sign-in (a replayed token?)")
        return claims

    def mfa_satisfied(self, claims: dict[str, Any]) -> bool:
        """US37.8: the IdP must say MFA happened -- an `amr` method, or an
        `acr` the deployment counts as MFA."""
        amr = claims.get("amr") or []
        if isinstance(amr, str):
            amr = [amr]
        if set(amr) & set(self.settings.auth_mfa_amr_values):
            return True
        return bool(claims.get("acr")) and claims.get("acr") in self.settings.auth_mfa_acr_values

    # -- afterwards: refresh, logout --

    def refresh(self, refresh_token: str) -> dict[str, Any] | None:
        """New IdP tokens, or None when the IdP refuses (account disabled,
        IdP session ended). Raises IdpUnavailable when it can't answer."""
        response = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        if response.status_code >= 500:
            raise IdpUnavailable(f"Token endpoint returned HTTP {response.status_code}")
        return response.json() if response.status_code == 200 else None

    def end_session_url(self, id_token_hint: str | None, post_logout_redirect_uri: str) -> str | None:
        try:
            endpoint = self.discovery().get("end_session_endpoint")
        except (OidcError, IdpUnavailable):
            return None
        if not endpoint:
            return None
        params = {"client_id": self.settings.oidc_client_id, "post_logout_redirect_uri": post_logout_redirect_uri}
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        return f"{endpoint}?{urlencode(params)}"

    def validate_logout_token(self, token: str) -> dict[str, Any]:
        """OIDC Back-Channel Logout 1.0, section 2.6."""
        claims = self._decode(token, require=["iss", "aud", "iat"])
        if BACKCHANNEL_LOGOUT_EVENT not in (claims.get("events") or {}):
            raise OidcError("invalid_token", "Not a back-channel logout token (no logout event)")
        if "nonce" in claims:
            raise OidcError("invalid_token", "A logout token must not carry a nonce")
        if not claims.get("sid") and not claims.get("sub"):
            raise OidcError("invalid_token", "A logout token must name a session (sid) or a user (sub)")
        return claims


_provider: OidcProvider | None = None
_provider_lock = threading.Lock()


def get_oidc_provider() -> OidcProvider:
    """FastAPI dependency (tests override it with a fake IdP). Built lazily:
    nothing is fetched until a sign-in needs it."""
    global _provider
    with _provider_lock:
        if _provider is None or _provider.settings is not get_settings():
            _provider = OidcProvider(get_settings())
        return _provider


# -- the IdP refresh token, kept encrypted ------------------------------------------


def _fernet(settings: Settings) -> Fernet:
    # Derived from the client secret: no extra key to configure, and a
    # rotated secret simply makes old sessions unable to confirm, so they
    # end at their next refresh.
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"axyntro idp refresh token").derive(
        settings.oidc_client_secret.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def seal(settings: Settings, token: str) -> str:
    return _fernet(settings).encrypt(token.encode()).decode()


def unseal(settings: Settings, sealed: str) -> str | None:
    try:
        return _fernet(settings).decrypt(sealed.encode()).decode()
    except InvalidToken:
        return None


# -- from verified claims to a user -------------------------------------------------


def group_names(claims: dict[str, Any], claim: str) -> list[str]:
    raw = claims.get(claim) or []
    if isinstance(raw, str):
        raw = [raw]
    # Keycloak can send full paths ("/axyntro-admins").
    return [str(g).lstrip("/") for g in raw]


def groups_overage(claims: dict[str, Any], claim: str) -> bool:
    """Entra ID drops the groups claim past ~200 groups and points to Graph
    instead (`_claim_names`). That's "too many to list", not "no groups"."""
    return claim in (claims.get("_claim_names") or {})


def _unique_name(db: Session, claims: dict[str, Any]) -> str:
    base = (claims.get("name") or claims.get("preferred_username") or claims.get("email") or claims["sub"]).strip()
    qualifier = claims.get("preferred_username") or claims.get("email") or claims["sub"][:8]
    for candidate in (base, f"{base} ({qualifier})", f"{base} ({claims['sub'][:8]})"):
        if db.scalar(select(UserModel.id).where(UserModel.name == candidate)) is None:
            return candidate
    return f"{base} ({new_id('u')})"


@dataclass
class SignedIn:
    user: UserModel
    created: bool
    detail: dict[str, Any] = field(default_factory=dict)


def _scim_user_for(db: Session, settings: Settings, claims: dict[str, Any]) -> UserModel | None:
    """US37.10: a user the IdP provisioned over SCIM who hasn't signed in
    yet, found by the claim that carries their SCIM externalId -- an
    immutable IdP id, never an email or a name."""
    value = claims.get(settings.scim_link_claim)
    if not value:
        return None
    candidates = db.scalars(
        select(UserModel).where(
            UserModel.provisioned_by == "scim", UserModel.scim_external_id == str(value),
            UserModel.scim_deleted_at.is_(None),
        )
    ).all()
    for user in candidates:
        linked = db.scalar(
            select(UserIdentityModel).where(
                UserIdentityModel.user_id == user.id, UserIdentityModel.issuer == claims["iss"]
            )
        )
        if linked is None:
            return user
    return None


def sign_in(db: Session, settings: Settings, claims: dict[str, Any]) -> SignedIn:
    """Finds the user for verified claims -- an earlier sign-in, a user
    provisioned over SCIM, or (with JIT provisioning) a new one -- and
    brings their group roles up to date. Raises OidcError to refuse."""
    issuer, subject = claims["iss"], claims["sub"]
    identity = db.get(UserIdentityModel, (issuer, subject))
    created = False
    detail: dict[str, Any] = {"issuer": issuer, "subject": subject}
    if identity is None:
        user = _scim_user_for(db, settings, claims)
        if user is not None:
            detail["linked_scim_user"] = True
        elif not settings.oidc_jit_provisioning:
            raise OidcError("not_provisioned", f"No account here for {claims.get('email') or subject}, and JIT provisioning is off")
        else:
            user = UserModel(id=new_id("user"), name=_unique_name(db, claims), provisioned_by="sso")
            db.add(user)
            db.flush()
            created = True
        identity = UserIdentityModel(issuer=issuer, subject=subject, user_id=user.id)
        db.add(identity)
    user = db.get(UserModel, identity.user_id)
    if user is None or not user.active:
        raise OidcError("deactivated", "This account is deactivated")
    identity.email = claims.get("email")
    identity.username = claims.get("preferred_username")
    identity.last_sign_in_at = utcnow().replace(tzinfo=None)

    claim = settings.oidc_groups_claim
    if user.provisioned_by == "scim":
        # The IdP's SCIM pushes own this user's groups; the claim may lag.
        detail["groups_from"] = "scim"
    elif groups_overage(claims, claim):
        # Keep the roles they had rather than silently stripping them.
        detail["groups_overage"] = True
    else:
        user.email = claims.get("email") or user.email
        user.idp_groups = group_names(claims, claim)
        detail["groups"] = user.idp_groups
    if not detail.get("groups_overage"):
        mappings = directory.load(db, settings)
        change = directory.apply(db, user, mappings)
        granted = directory.grants_for(directory.user_groups(db, user), mappings)
        detail["roles"] = [f"{r}@{t}:{i}".rstrip(":") for r, t, i in granted]
        if change.gained or change.lost:
            detail["roles_changed"] = True
    db.flush()
    return SignedIn(user=user, created=created, detail=detail)


def safe_return_to(value: str | None) -> str:
    """Only a path on our own frontend -- never "//evil.example" or a URL."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or re.search(r"[\r\n]", value):
        return "/"
    return value


def login_request_expired(created_at: datetime, now: datetime) -> bool:
    return (now - created_at).total_seconds() > 600
