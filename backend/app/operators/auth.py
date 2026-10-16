"""Epic 38, US38.8: operators' sign-in, sessions and the platform audit log."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import secret_store
from app.config import Settings, get_settings
from app.db.models import OperatorModel, OperatorSessionModel, PlatformAuditEventModel
from app.ids import new_id, utcnow
from app.tenancy.identity import IdentityConfig, with_host

COOKIE = "asg_operator"
COOKIE_PATH = "/api/operator"
LOCAL_ISSUER = "local"


def now() -> datetime:
    return utcnow().replace(tzinfo=None)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def identity_config(settings: Settings, host: str) -> IdentityConfig:
    """The platform's IdP, in the shape app/auth/oidc.py's provider takes.
    MFA is always required for operators."""
    api_url = with_host(settings.api_public_url, host)
    return IdentityConfig(
        auth_mode="oidc" if settings.operator_auth_mode == "oidc" else "local", source="environment",
        tenant_id="platform", issuer=settings.operator_oidc_issuer, discovery_url=settings.operator_oidc_discovery_url,
        client_id=settings.operator_oidc_client_id, client_secret_ref=f"env:{settings.operator_oidc_client_secret_env}", secret_scope=secret_store.PLATFORM,
        display_name="platform SSO", acr_values=settings.operator_oidc_acr_values, require_mfa=True,
        mfa_amr_values=tuple(settings.operator_mfa_amr_values), mfa_acr_values=tuple(settings.operator_mfa_acr_values),
        redirect_uri=api_url + "/api/operator/oidc/callback", frontend_url=with_host(settings.frontend_url, host),
        api_url=api_url,
    )


# ---- operators and sessions --------------------------------------------------------


def operator_for(db: Session, *, issuer: str, subject: str, name: str, email: str | None) -> OperatorModel:
    row = db.scalar(select(OperatorModel).where(OperatorModel.issuer == issuer, OperatorModel.subject == subject))
    if row is None:
        row = OperatorModel(id=new_id("op"), issuer=issuer, subject=subject, name=name, email=email)
        db.add(row)
    row.name, row.email, row.last_sign_in_at = name, email, now()
    db.flush()
    return row


@dataclass
class Started:
    session: OperatorSessionModel
    token: str


def start(db: Session, operator: OperatorModel, auth_method: str, id_token: str | None = None) -> Started:
    current = now()
    db.execute(delete(OperatorSessionModel).where(OperatorSessionModel.expires_at < current - timedelta(days=1)))
    token = secrets.token_urlsafe(32)
    record = OperatorSessionModel(
        id=new_id("opsession"), operator_id=operator.id, token_hash=_hash(token), csrf_token=secrets.token_urlsafe(32),
        auth_method=auth_method, created_at=current, idp_id_token=id_token,
        expires_at=current + timedelta(hours=get_settings().operator_session_hours),
    )
    db.add(record)
    db.flush()
    return Started(session=record, token=token)


def authenticate(db: Session, token: str) -> OperatorSessionModel | None:
    record = db.scalar(select(OperatorSessionModel).where(OperatorSessionModel.token_hash == _hash(token)))
    if record is None or record.revoked_at is not None or record.expires_at <= now():
        return None
    return record


def csrf_ok(record: OperatorSessionModel, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(record.csrf_token, header_value)


def set_cookie(response: Response, started: Started) -> None:
    response.set_cookie(
        COOKIE, started.token, max_age=get_settings().operator_session_hours * 3600, path=COOKIE_PATH,
        httponly=True, samesite="lax", secure=get_settings().cookie_secure,
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path=COOKIE_PATH, httponly=True, samesite="lax", secure=get_settings().cookie_secure)


# ---- the platform audit log --------------------------------------------------------


def record(
    db: Session, *, operator: OperatorModel | None, action: str, request: Request | None = None,
    tenant_id: str | None = None, outcome: str = "success", detail: dict[str, Any] | None = None,
    operator_name: str | None = None,
) -> None:
    db.add(PlatformAuditEventModel(
        id=new_id("paudit"), operator_id=operator.id if operator else None,
        operator_name=operator.name if operator else (operator_name or "unknown"), action=action, outcome=outcome,
        tenant_id=tenant_id, ip=request.client.host if request is not None and request.client else None,
        detail=detail or {},
    ))
