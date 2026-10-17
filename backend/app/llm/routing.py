"""Epic 31, US31.8: per-business-unit model and provider policy.

Regulated units often may not send policy text, system topology, or
customer data to an arbitrary hosted model. A business unit's (or the
organization's) `llm_policy` says which providers are allowed, where they
must run (residency), whether to use a self-hosted OpenAI-compatible
endpoint instead, and whether to redact personal data first.

The policy for the process being worked on is put in a context variable
(`llm_route_for`), and `LLMClient.complete` honours it for every call made
inside -- so call sites don't each have to thread a route through.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.schemas.context import LlmPolicy

__all__ = ["LlmPolicy", "LlmRoute", "current_route", "llm_route_for", "redact", "route_for_scope", "route_from_policy"]


@dataclass(frozen=True)
class LlmRoute:
    base_url: str | None = None
    api_key_env: str | None = None
    model: str | None = None
    redact_pii: bool = False
    blocked_reason: str | None = None
    business_unit: str | None = None
    # Epic 38, US38.15: where a self-hosted endpoint runs (`base_url`).
    provider_region: str | None = None


current_route: ContextVar[LlmRoute | None] = ContextVar("current_llm_route", default=None)


def route_from_policy(policy: LlmPolicy, *, unit_name: str | None) -> LlmRoute:
    settings = get_settings()
    if policy.base_url:
        provider, region = policy.provider_name or "self_hosted", policy.provider_region or ""
    else:
        provider, region = settings.llm_provider_name, settings.llm_provider_region
    who = f"'{unit_name}'" if unit_name else "this organization"
    if policy.allowed_providers and provider not in policy.allowed_providers:
        return LlmRoute(
            blocked_reason=f"{who} does not allow the '{provider}' model provider (allowed: {', '.join(policy.allowed_providers)})",
            business_unit=unit_name,
        )
    if policy.residency and region.upper() not in {r.upper() for r in policy.residency}:
        return LlmRoute(
            blocked_reason=(
                f"{who} requires models hosted in {', '.join(policy.residency)}; "
                f"'{provider}' is {'hosted in ' + region if region else 'of unknown hosting region'}"
            ),
            business_unit=unit_name,
        )
    return LlmRoute(
        base_url=policy.base_url,
        api_key_env=policy.api_key_env,
        model=policy.model,
        redact_pii=policy.redact_pii,
        business_unit=unit_name,
        provider_region=policy.provider_region if policy.base_url else None,
    )


def route_for_scope(session: Any, business_unit_id: str | None) -> LlmRoute | None:
    """The unit's policy if it has one, else the organization's, else None
    (no policy: calls go out exactly as before)."""
    from app.context import repository as context_repository

    unit = context_repository.get_entity_model(session, business_unit_id) if business_unit_id else None
    raw = (unit.data or {}).get("llm_policy") if unit is not None else None
    if not raw:
        raw = (context_repository.get_organization_model(session).profile or {}).get("llm_policy")
    if not raw:
        return None
    return route_from_policy(LlmPolicy.model_validate(raw), unit_name=unit.name if unit is not None else None)


@contextmanager
def llm_route_for(session: Any, *, process_id: str | None = None, business_unit_id: str | None = None):
    """Applies the right policy to every LLM call made inside the block."""
    if process_id is not None and business_unit_id is None:
        from app.context import repository as context_repository

        business_unit_id = context_repository.get_process_scope(session, process_id).business_unit_id
    token = current_route.set(route_for_scope(session, business_unit_id))
    try:
        yield
    finally:
        current_route.reset(token)


# ---- redaction ------------------------------------------------------------

_PATTERNS = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("NINO", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b")),
    ("PHONE", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d\b")),
]


def redact(text: str) -> str:
    for label, pattern in _PATTERNS:
        text = pattern.sub(f"[{label} REDACTED]", text)
    return text
