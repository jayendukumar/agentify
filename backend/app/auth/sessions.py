"""Epic 37, US37.12-37.13: short, rotating, revocable sessions.

The browser holds two httponly cookies: a session (access) token good for
`auth_access_token_minutes`, and a refresh token sent only to
`/api/auth/refresh`, which swaps it for a new pair. Both are random and
stored only as SHA-256 hashes. Every request checks the session row, so a
revocation takes effect on the next request.

Refresh tokens rotate on every use. Presenting one that was already
rotated means two parties hold it -- the browser and whoever copied it --
so the whole session is revoked. The one exception is a reuse within
`REUSE_GRACE` of the rotation: two tabs that hit a 401 at the same moment
both refresh with the same token. That second refresh gets no new tokens
(the first response already set them in the shared cookie jar), so the
grace period hands nothing to a thief.

State-changing requests also need the session's CSRF token in a header
(`CSRF_HEADER`), since cookies are now the only credential.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import SessionModel, SessionTokenModel, UserModel
from app.ids import new_id, utcnow

ACCESS_COOKIE = "asg_session"
REFRESH_COOKIE = "asg_refresh"
REFRESH_PATH = "/api/auth/refresh"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

REUSE_GRACE = timedelta(seconds=10)
# last_used_at drives the idle timeout; writing it on every request would
# turn every GET into a write, and a minute is plenty of resolution.
_TOUCH_EVERY = timedelta(seconds=60)


def _now() -> datetime:
    # Naive UTC, like every timestamp column here (Postgres/psycopg return
    # them naive), so comparisons after a DB round-trip don't raise.
    return utcnow().replace(tzinfo=None)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _idle_timeout() -> timedelta:
    return timedelta(minutes=get_settings().auth_idle_timeout_minutes)


def is_alive(record: SessionModel, now: datetime | None = None) -> bool:
    now = now or _now()
    return record.revoked_at is None and record.expires_at > now and record.last_used_at + _idle_timeout() > now


@dataclass
class Issued:
    session: SessionModel
    access_token: str
    refresh_token: str


def _issue_tokens(db: Session, record: SessionModel, now: datetime) -> Issued:
    settings = get_settings()
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    access_expires = min(now + timedelta(minutes=settings.auth_access_token_minutes), record.expires_at)
    db.add_all([
        SessionTokenModel(
            token_hash=_hash(access), session_id=record.id, kind="access", issued_at=now, expires_at=access_expires
        ),
        SessionTokenModel(
            token_hash=_hash(refresh), session_id=record.id, kind="refresh", issued_at=now, expires_at=record.expires_at
        ),
    ])
    db.flush()
    return Issued(session=record, access_token=access, refresh_token=refresh)


def _client(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    return (request.client.host if request.client else None), request.headers.get("user-agent")


def start(
    db: Session, user: UserModel, request: Request | None = None, *, auth_method: str = "local", **idp: Any
) -> Issued:
    """A new sign-in. Also clears out this user's dead sessions, so the
    table holds roughly one row per live browser. `idp` sets the
    SessionModel `idp_*` columns of an SSO sign-in."""
    now = _now()
    for old in db.scalars(select(SessionModel).where(SessionModel.user_id == user.id)):
        if not is_alive(old, now):
            db.delete(old)
    ip, user_agent = _client(request)
    record = SessionModel(
        id=new_id("session"),
        user_id=user.id,
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(hours=get_settings().auth_session_max_hours),
        csrf_token=secrets.token_urlsafe(32),
        ip=ip,
        user_agent=user_agent,
        auth_method=auth_method,
        **idp,
    )
    db.add(record)
    db.flush()
    return _issue_tokens(db, record, now)


def authenticate(db: Session, access_token: str) -> SessionModel | None:
    """The live session of an active user behind a session cookie, or None."""
    now = _now()
    token = db.get(SessionTokenModel, _hash(access_token))
    if token is None or token.kind != "access" or token.rotated_at is not None or token.expires_at <= now:
        return None
    record = db.get(SessionModel, token.session_id)
    if record is None or not is_alive(record, now):
        return None
    user = db.get(UserModel, record.user_id)
    if user is None or not user.active:
        return None
    if now - record.last_used_at >= _TOUCH_EVERY:
        record.last_used_at = now
        db.flush()
    return record


def csrf_ok(record: SessionModel, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(record.csrf_token, header_value)


class RefreshOutcome(enum.Enum):
    ISSUED = "issued"  # new tokens
    RACED = "raced"  # a concurrent refresh already rotated this token; no new tokens
    REUSED = "reused"  # an old refresh token came back -- the session is now revoked
    INVALID = "invalid"


@dataclass
class RefreshResult:
    outcome: RefreshOutcome
    session: SessionModel | None = None
    user: UserModel | None = None
    issued: Issued | None = None


def refresh(
    db: Session, refresh_token: str, confirm: Callable[[Session, SessionModel], bool] | None = None
) -> RefreshResult:
    """`confirm` (SSO sessions): asked before new tokens are issued whether
    the account still exists at the IdP; False ends the session."""
    now = _now()
    token = db.get(SessionTokenModel, _hash(refresh_token))
    if token is None or token.kind != "refresh":
        return RefreshResult(RefreshOutcome.INVALID)
    record = db.get(SessionModel, token.session_id)
    if record is None or not is_alive(record, now):
        return RefreshResult(RefreshOutcome.INVALID, session=record)
    user = db.get(UserModel, record.user_id)
    if token.rotated_at is not None:
        if now - token.rotated_at <= REUSE_GRACE and user is not None and user.active:
            return RefreshResult(RefreshOutcome.RACED, session=record, user=user)
        revoke(db, record, "refresh_token_reuse")
        return RefreshResult(RefreshOutcome.REUSED, session=record, user=user)
    if user is None or not user.active:
        return RefreshResult(RefreshOutcome.INVALID, session=record)
    if confirm is not None and not confirm(db, record):
        revoke(db, record, "idp_session_ended")
        return RefreshResult(RefreshOutcome.INVALID, session=record, user=user)
    # The old pair stops working the moment the new one exists.
    db.execute(
        update(SessionTokenModel)
        .where(SessionTokenModel.session_id == record.id, SessionTokenModel.rotated_at.is_(None))
        .values(rotated_at=now)
    )
    record.last_used_at = now
    return RefreshResult(RefreshOutcome.ISSUED, session=record, user=user, issued=_issue_tokens(db, record, now))


def revoke(db: Session, record: SessionModel, reason: str) -> None:
    """Ends a session. The row stays, marked revoked, until the user's
    next sign-in clears it (`start`); its tokens go now."""
    if record.revoked_at is None:
        record.revoked_at = _now()
        record.revoked_reason = reason
    db.execute(delete(SessionTokenModel).where(SessionTokenModel.session_id == record.id))
    db.flush()


def revoke_all(db: Session, user_id: str, reason: str) -> int:
    revoked = 0
    for record in active_sessions(db, user_id):
        revoke(db, record, reason)
        revoked += 1
    return revoked


def active_sessions(db: Session, user_id: str) -> list[SessionModel]:
    now = _now()
    rows = db.scalars(
        select(SessionModel).where(SessionModel.user_id == user_id).order_by(SessionModel.last_used_at.desc())
    )
    return [r for r in rows if is_alive(r, now)]


def set_cookies(response: Response, issued: Issued) -> None:
    settings = get_settings()
    common = {"httponly": True, "samesite": "lax", "secure": settings.cookie_secure}
    response.set_cookie(
        ACCESS_COOKIE, issued.access_token, max_age=settings.auth_access_token_minutes * 60, path="/", **common
    )
    remaining = int((issued.session.expires_at - _now()).total_seconds())
    response.set_cookie(REFRESH_COOKIE, issued.refresh_token, max_age=max(remaining, 0), path=REFRESH_PATH, **common)


def clear_cookies(response: Response) -> None:
    secure = get_settings().cookie_secure
    response.delete_cookie(ACCESS_COOKIE, path="/", httponly=True, samesite="lax", secure=secure)
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH, httponly=True, samesite="lax", secure=secure)
