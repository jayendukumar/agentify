"""Epic 29, US29.5: production safety for twin API mode.

A twin run may call a real HTTP endpoint only when every one of these
holds -- checked when the endpoint is registered *and* again immediately
before every call (an endpoint can't become callable by being edited
around the first check):

1. The endpoint carries an explicit environment tag, and it is not
   "production".
2. The URL is http(s) and its host doesn't look like production: no host
   label is "prod", "production", "live", or starts with "prod-"/"prd".
3. The host is on the allow-list: loopback / docker-internal / `.local`
   hosts, a host with a non-production label ("sandbox", "test", "dev",
   "staging", "mock", "uat", "qa"), or a pattern in the
   `twin_api_allowed_hosts` setting.

So an untagged endpoint, a production-tagged one, or a plausible-looking
host nobody allow-listed is refused. See planning/decision-log.md
(2026-10-11) for why this is a deny-by-default allow-list rather than a
production deny-list.
"""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse

from app.config import get_settings

SAFE_ENVIRONMENTS = {"sandbox", "test", "dev", "staging", "mock"}
_SAFE_LABELS = {"sandbox", "test", "testing", "dev", "development", "staging", "stage", "mock", "uat", "qa"}
_PRODUCTION_LABELS = {"prod", "production", "live", "prd"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class UnsafeEndpointError(Exception):
    pass


def _labels(host: str) -> list[str]:
    parts: list[str] = []
    for label in host.lower().split("."):
        parts.extend(p for p in label.replace("_", "-").split("-") if p)
    return parts


def check_endpoint(url: str, environment: str | None) -> None:
    if not environment:
        raise UnsafeEndpointError("Endpoint has no environment tag -- tag it sandbox, test, dev, staging, or mock")
    if environment not in SAFE_ENVIRONMENTS:
        raise UnsafeEndpointError(f"Endpoint is tagged '{environment}' -- twin runs only call non-production endpoints")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeEndpointError(f"'{url}' is not an http(s) URL")
    host = parsed.hostname.lower()
    labels = _labels(host)
    if any(label in _PRODUCTION_LABELS or label.startswith("prod") for label in labels):
        raise UnsafeEndpointError(f"Host '{host}' looks like production -- refused even though it is tagged '{environment}'")

    allowed_patterns = [p.strip().lower() for p in get_settings().twin_api_allowed_hosts.split(",") if p.strip()]
    if (
        host in _LOCAL_HOSTS
        or host.endswith(".local")
        or host.endswith(".localhost")
        or any(label in _SAFE_LABELS for label in labels)
        or any(fnmatch.fnmatch(host, pattern) for pattern in allowed_patterns)
    ):
        return
    raise UnsafeEndpointError(
        f"Host '{host}' is not recognisably non-production -- add it to TWIN_API_ALLOWED_HOSTS to allow twin calls"
    )


def is_safe(url: str, environment: str | None) -> bool:
    try:
        check_endpoint(url, environment)
    except UnsafeEndpointError:
        return False
    return True
