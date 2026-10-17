"""Epic 39, US39.16: an agent started by an event in the tenant's systems.

A subscription belongs to one deployment. Its system POSTs the event to
`/runtime/v1/events/{subscription id}` with two headers:

    <timestamp_header>: <unix seconds>
    <signature_header>: <signature_prefix><hex HMAC-SHA256 of "<timestamp>.<raw body>">

The same scheme Stripe and Slack use. The key is the secret the
subscription references (`secret://tenant/<name>`, resolved in the
tenant's store when the event arrives). An event is accepted only when
the signature checks out and the timestamp is within five minutes, so a
captured event can't be replayed later. The event id (`event_id_path`)
is the idempotency key, so a retried delivery starts nothing new.

The event becomes the agent's input through Epic 31's dotted-path
mapping ({input field: path}), then goes through the same `submit` as an
API call -- routing, caps, input checks, canary and shadow alike. An
event that doesn't match `event_filter` is acknowledged and ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app import secret_store
from app.connectors.mapping import value_at
from app.db.models import AgentDeploymentModel, AgentExecutionModel, EventSubscriptionModel
from app.ids import utcnow
from app.schemas.agents import AgentDefinition
from app.tenancy.identity import current_slug

from . import jobs

REPLAY_WINDOW_SECONDS = 300
MAX_BODY_BYTES = 256 * 1024


class EventRefused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass
class Received:
    execution: AgentExecutionModel | None
    created: bool
    ignored: str | None = None


# ---- setting one up ----------------------------------------------------------------


def check_subscription(deployment: AgentDeploymentModel, *, secret_ref: str, input_mapping: dict[str, str],
                       tenant_slug: str) -> list[str]:
    """Problems with a subscription before it's saved."""
    problems = []
    try:
        secret_store.check_ref(secret_ref, tenant_slug, example="secret://tenant/loan_events_secret")
    except secret_store.SecretRefError as exc:
        problems.append(f"secret_ref: {exc}")
    fields = {f.name for f in AgentDefinition.model_validate(deployment.snapshot["definition"]).input_schema}
    unknown = sorted(set(input_mapping) - fields)
    if unknown:
        problems.append(f"input_mapping names fields the agent doesn't take: {', '.join(unknown)}")
    missing = sorted(fields - set(input_mapping))
    if missing:
        problems.append(f"input_mapping leaves out input fields: {', '.join(missing)}")
    if not all(isinstance(p, str) and p for p in input_mapping.values()):
        problems.append("each input_mapping value is a dotted path into the event, like data.customer.id")
    return problems


# ---- receiving one -----------------------------------------------------------------------


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify(subscription: EventSubscriptionModel, headers: dict[str, str], body: bytes, *, now: float | None = None) -> None:
    """Raises EventRefused (401) unless the event is signed with the
    subscription's secret within the replay window."""
    secret = secret_store.resolve(subscription.secret_ref, current_slug())
    if not secret:
        raise EventRefused(503, "The subscription's signing secret isn't set on the server")
    timestamp = headers.get(subscription.timestamp_header.lower(), "")
    given = headers.get(subscription.signature_header.lower(), "").removeprefix(subscription.signature_prefix)
    if not timestamp.isdigit() or not given:
        raise EventRefused(401, f"Sign the event: {subscription.timestamp_header} and {subscription.signature_header}")
    if abs((now or time.time()) - int(timestamp)) > REPLAY_WINDOW_SECONDS:
        raise EventRefused(401, "The event's timestamp is outside the 5-minute window")
    if not hmac.compare_digest(given, signature(secret, timestamp, body)):
        raise EventRefused(401, "The signature doesn't match")


def _parse(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_BODY_BYTES:
        raise EventRefused(413, "The event is larger than 256 KB")
    try:
        event = json.loads(body)
    except ValueError as exc:
        raise EventRefused(400, "The event isn't JSON") from exc
    if not isinstance(event, dict):
        raise EventRefused(400, "The event must be a JSON object")
    return event


def _filtered_out(subscription: EventSubscriptionModel, event: dict[str, Any]) -> str | None:
    for path, expected in (subscription.event_filter or {}).items():
        if value_at(event, path) != expected:
            return f"{path} is {value_at(event, path)!r}, not {expected!r}"
    return None


def map_input(subscription: EventSubscriptionModel, event: dict[str, Any]) -> dict[str, Any]:
    """The agent's input; a field whose path isn't in the event is left
    out, so the input check names it."""
    mapped = {name: value_at(event, path) for name, path in subscription.input_mapping.items()}
    return {name: value for name, value in mapped.items() if value is not None}


def receive(session: Session, subscription: EventSubscriptionModel, headers: dict[str, str], body: bytes) -> Received:
    if subscription.revoked_at is not None:
        raise EventRefused(404, f"Subscription '{subscription.id}' not found")
    verify(subscription, headers, body)
    event = _parse(body)
    ignored = _filtered_out(subscription, event)
    if ignored:
        return Received(execution=None, created=False, ignored=ignored)
    event_id = value_at(event, subscription.event_id_path)
    if event_id in (None, ""):
        raise EventRefused(422, f"The event has no id at '{subscription.event_id_path}'")
    deployment = session.get(AgentDeploymentModel, subscription.deployment_id)
    caller = jobs.Caller(client_id=None, label=f"event:{subscription.label}", trigger="event")
    try:
        execution, created = jobs.submit(session, deployment, caller, data=map_input(subscription, event),
                                         idempotency_key=f"event:{subscription.id}:{event_id}"[:200],
                                         callback_url=None)
    except jobs.SubmitError as exc:
        raise EventRefused(exc.status, exc.detail) from exc
    subscription.last_event_at = utcnow()
    return Received(execution=execution, created=created)
