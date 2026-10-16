"""Epic 37, US37.7-37.8 and US37.13: sign-in through the customer's IdP
over OpenID Connect, and the IdP telling us a session ended.

The browser is sent to the IdP by `/login` and comes back to `/callback`.
A refused sign-in redirects to the frontend with `?auth_error=<code>`
(the sign-in page explains it) and writes a refused audit event; it never
shows a raw error page. See app/auth/oidc.py for what's checked.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import delete, select

from app.auth import audit, oidc, sessions
from app.config import get_settings
from app.db.models import OidcLoginRequestModel, SessionModel
from app.db.session import get_session_factory
from app.ids import utcnow
from app.tenancy import identity, tenant_scope
from app.tenancy.hosts import tenant_ids
from app.tenancy.identity import IdentityConfig, get_identity

from .deps import DbDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])

OidcDep = Annotated[oidc.OidcProvider, Depends(oidc.get_oidc_provider)]
# Epic 38, US38.6: this tenant's IdP, MFA rules and URLs.
IdentityDep = Annotated[IdentityConfig, Depends(get_identity)]
STATE_COOKIE = "asg_oidc_state"
_STATE_PATH = "/api/auth/oidc"


def _require_oidc(config: IdentityConfig) -> None:
    if config.auth_mode != "oidc":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Single sign-on isn't set up for this organization")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now():
    return utcnow().replace(tzinfo=None)


def _to_frontend(config: IdentityConfig, path: str = "/", **query: str) -> str:
    url = config.frontend_url.rstrip("/") + path
    return f"{url}{'&' if '?' in url else '?'}{urlencode(query)}" if query else url


@router.get("/login")
def oidc_login(db: DbDep, provider: OidcDep, config: IdentityDep, return_to: str = "/") -> Response:
    """Starts a sign-in: remembers a fresh state, nonce and PKCE verifier,
    binds the state to this browser with a short-lived cookie, and sends
    the browser to the IdP."""
    _require_oidc(config)
    now = _now()
    db.execute(delete(OidcLoginRequestModel).where(OidcLoginRequestModel.created_at < now - timedelta(minutes=10)))
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    try:
        url = provider.authorization_url(state=state, nonce=nonce, code_verifier=verifier)
    except (oidc.OidcError, oidc.IdpUnavailable) as exc:
        logger.error("OIDC sign-in can't start: %s", exc)
        return RedirectResponse(_to_frontend(config, auth_error="idp_unavailable"), status.HTTP_302_FOUND)
    db.add(OidcLoginRequestModel(
        state_hash=_hash(state), nonce=nonce, code_verifier=verifier, return_to=oidc.safe_return_to(return_to),
        created_at=now,
    ))
    response = RedirectResponse(url, status.HTTP_302_FOUND)
    response.set_cookie(
        STATE_COOKIE, state, max_age=600, path=_STATE_PATH, httponly=True, samesite="lax",
        secure=get_settings().cookie_secure,
    )
    return response


@router.get("/callback")
def oidc_callback(
    request: Request,
    db: DbDep,
    provider: OidcDep,
    config: IdentityDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    asg_oidc_state: Annotated[str | None, Cookie()] = None,
) -> Response:
    """Finishes a sign-in. Every refusal is returned (not raised) so its
    audit event commits."""
    _require_oidc(config)

    def refuse(code_: str, reason: str, *, who: str | None = None, detail: dict | None = None) -> Response:
        audit.record(
            db, actor=None, actor_name=who or "unknown (SSO)", action="sign_in", outcome="refused", request=request,
            detail={"mode": "oidc", "error": code_, "reason": reason, **(detail or {})},
        )
        response = RedirectResponse(_to_frontend(config, auth_error=code_), status.HTTP_302_FOUND)
        response.delete_cookie(STATE_COOKIE, path=_STATE_PATH)
        return response

    if error:
        return refuse("idp_error", f"The IdP returned {error}: {error_description or ''}".strip())
    # The state must match the cookie this browser got at /login: a callback
    # carrying someone else's code (login CSRF) has no such cookie.
    if not code or not state or not asg_oidc_state or not hmac.compare_digest(state, asg_oidc_state):
        return refuse("state_mismatch", "The sign-in response doesn't belong to a sign-in started in this browser")
    pending = db.get(OidcLoginRequestModel, _hash(state))
    if pending is None:
        return refuse("expired", "Unknown or already used sign-in state")
    db.delete(pending)
    if oidc.login_request_expired(pending.created_at, _now()):
        return refuse("expired", "The sign-in took longer than ten minutes")

    try:
        tokens = provider.exchange_code(code, pending.code_verifier)
        claims = provider.validate_id_token(tokens["id_token"], nonce=pending.nonce)
    except oidc.OidcError as exc:
        return refuse(exc.code, str(exc))
    except oidc.IdpUnavailable as exc:
        return refuse("idp_unavailable", str(exc))

    who = claims.get("email") or claims.get("preferred_username") or claims["sub"]
    if config.require_mfa and not provider.mfa_satisfied(claims):
        return refuse(
            "mfa_required", "The IdP didn't report multi-factor authentication for this sign-in", who=who,
            detail={"amr": claims.get("amr"), "acr": claims.get("acr")},
        )
    try:
        result = oidc.sign_in(db, config, claims)
    except oidc.OidcError as exc:
        return refuse(exc.code, str(exc), who=who)

    refresh_token = tokens.get("refresh_token")
    issued = sessions.start(
        db, result.user, request, auth_method="oidc",
        idp_issuer=claims["iss"], idp_subject=claims["sub"], idp_sid=claims.get("sid"),
        idp_id_token=tokens["id_token"], idp_refresh_token=oidc.seal(config, refresh_token) if refresh_token else None,
    )
    audit.record(
        db, actor=result.user, action="sign_in", object_type="user", object_id=result.user.id, request=request,
        detail={"mode": "oidc", "new_user": result.created, "session_id": issued.session.id,
                "amr": claims.get("amr"), "acr": claims.get("acr"), **result.detail},
    )
    response = RedirectResponse(_to_frontend(config, pending.return_to), status.HTTP_302_FOUND)
    sessions.set_cookies(response, issued)
    response.delete_cookie(STATE_COOKIE, path=_STATE_PATH)
    return response


def _addressed_tenants(issuer: str, audience: list[str]) -> list[IdentityConfig]:
    """US38.7: the tenants whose IdP is this issuer, with this client.
    Walks the tenants one at a time, under each one's row-level security,
    as the job worker does; a lookup across tenants would need a role that
    bypasses it, for a request nobody has authenticated yet."""
    factory = get_session_factory()
    with factory() as db:
        candidates = tenant_ids(db)
    found = []
    for tenant_id in candidates:
        with tenant_scope(tenant_id), factory() as db:
            config = identity.resolve(db, tenant_id=tenant_id)
        if config.auth_mode == "oidc" and config.issuer == issuer and config.client_id in audience:
            found.append(config)
    return found


def _end_idp_sessions(config: IdentityConfig, claims: dict, request: Request) -> int:
    with tenant_scope(config.tenant_id), get_session_factory()() as db, db.begin():
        query = select(SessionModel).where(SessionModel.idp_issuer == claims["iss"])
        query = query.where(SessionModel.idp_sid == claims["sid"]) if claims.get("sid") else query.where(
            SessionModel.idp_subject == claims["sub"]
        )
        ended = [record for record in db.scalars(query) if sessions.is_alive(record)]
        for record in ended:
            sessions.revoke(db, record, "idp_logout")
        audit.record(
            db, actor=None, actor_name="identity provider", action="idp_logout", object_type="user",
            object_id=ended[0].user_id if ended else None, request=request,
            detail={"sid": claims.get("sid"), "sub": claims.get("sub"), "sessions_revoked": len(ended)},
        )
        return len(ended)


@router.post("/backchannel-logout")
def backchannel_logout(
    request: Request, logout_token: Annotated[str, Form()],
    http: Annotated[httpx.Client | None, Depends(oidc.get_idp_http)],
) -> Response:
    """US37.13: the IdP says a user signed out there (or an admin ended
    their IdP session). Authenticated by the logout token's signature, not
    by a cookie -- the IdP calls this server to server.

    Epic 38, US38.7: the tenant comes from the token, not the host. An IdP
    may call any host that reaches the platform (Keycloak in Docker calls
    `api`), so the unverified issuer and audience only pick the tenants
    that trust that IdP and client; each one then checks the signature
    against its own IdP's keys, and only its own sessions end."""
    refused = JSONResponse({"error": "invalid_request"}, status.HTTP_400_BAD_REQUEST, headers={"Cache-Control": "no-store"})
    try:
        unverified = jwt.decode(logout_token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        logger.warning("Rejected back-channel logout: malformed token (%s)", exc)
        return refused
    audience = unverified.get("aud")
    audience = [audience] if isinstance(audience, str) else [a for a in audience or [] if isinstance(a, str)]
    configs = _addressed_tenants(str(unverified.get("iss") or ""), audience)
    if not configs:
        logger.warning("Rejected back-channel logout: no tenant trusts issuer %r for %r", unverified.get("iss"), audience)
        return refused
    accepted = False
    for config in configs:
        provider = oidc.OidcProvider(config, http=http) if http is not None else oidc.provider_for(config)
        try:
            claims = provider.validate_logout_token(logout_token)
        except (oidc.OidcError, oidc.IdpUnavailable) as exc:
            logger.warning("Rejected back-channel logout for tenant %s: %s", config.tenant_id, exc)
            continue
        _end_idp_sessions(config, claims, request)
        accepted = True
    return Response(status_code=200, headers={"Cache-Control": "no-store"}) if accepted else refused

