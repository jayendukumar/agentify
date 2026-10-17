"""Epic 39, US39.14: traces and metrics to the deployment's own collector.

When a case ends, a `runtime_telemetry` job sends it to the OTLP/HTTP
endpoint the deployment names (`telemetry.otlp_endpoint`, e.g. an
OpenTelemetry Collector at https://otel.example.com:4318) as OTLP JSON:

- **a trace:** a root span for the execution and one span per step, with
  the tenant, deployment, agent version and identity as attributes. No
  inputs, outputs, tool arguments or responses -- payloads stay here;
- **metrics:** the case counted by status, and its cost, tokens and
  duration, as delta sums and a gauge.

Built by hand rather than with the OpenTelemetry SDK: the SDK batches in
a background thread per process, while this runs once per case in a job
that retries (5 attempts) until the collector accepts it. The trace id is
derived from the execution id, so a retry sends the same trace.

The endpoint follows the production rule (https, or a RUNTIME_HTTP_HOSTS
host locally). Header values (an API key for a hosted collector) are
secret references, resolved in the tenant's store when sending.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import secret_store
from app.db.models import AgentDeploymentModel, AgentExecutionModel, ExecutionStepModel
from app.jobs import queue
from app.jobs.worker import handler
from app.tenancy.context import current_tenant_id
from app.tenancy.identity import current_slug

from .adapters import UnsafeProductionEndpoint, check_production_url

JOB_KIND = "runtime_telemetry"
SERVICE_NAME = "axyntro-agent-runtime"


class TelemetryError(ValueError):
    pass


def check_settings(telemetry: dict[str, Any]) -> None:
    endpoint = (telemetry or {}).get("otlp_endpoint")
    if not endpoint:
        return
    try:
        check_production_url(endpoint)
    except UnsafeProductionEndpoint as exc:
        raise TelemetryError(f"otlp_endpoint: {exc}") from exc
    for name, ref in ((telemetry or {}).get("headers") or {}).items():
        if not str(ref).startswith("secret://"):
            raise TelemetryError(f"header '{name}' must be a secret reference (secret://tenant/<name>), not a value")


def kept(telemetry: dict[str, Any]) -> dict[str, Any]:
    """The settings that are stored: the endpoint and header references."""
    return {k: v for k, v in telemetry.items() if k in ("otlp_endpoint", "headers") and v}


def queue_export(session: Session, deployment: AgentDeploymentModel, execution: AgentExecutionModel) -> None:
    if (deployment.telemetry or {}).get("otlp_endpoint"):
        queue.enqueue(session, JOB_KIND, {"execution_id": execution.id}, max_attempts=5,
                      dedupe_key=f"runtime-telemetry:{execution.id}")


def _nanos(at: datetime | None) -> str:
    if at is None:
        return "0"
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return str(int(at.timestamp() * 1_000_000_000))


def _id(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:length]


def _attrs(values: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, float):
            out.append({"key": key, "value": {"doubleValue": value}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out


def _resource(deployment: AgentDeploymentModel, execution: AgentExecutionModel) -> dict[str, Any]:
    return {"attributes": _attrs({
        "service.name": SERVICE_NAME,
        "axyntro.tenant": current_tenant_id(),
        "axyntro.deployment": deployment.id,
        "axyntro.agent": deployment.snapshot["definition"]["name"],
        "axyntro.agent_version": deployment.artifact_version,
        "axyntro.identity": execution.identity_id,
    })}


def _status_code(failed: bool, ok: int) -> dict[str, int]:
    return {"code": 2 if failed else ok}


def _step_span(trace_id: str, root_id: str, step: ExecutionStepModel, end: datetime | None) -> dict[str, Any]:
    body = step.step or {}
    return {
        "traceId": trace_id, "spanId": _id(f"span:{step.id}", 16), "parentSpanId": root_id,
        "name": step.type.lower() + (f" {step.target}" if step.target else ""), "kind": 1,
        "startTimeUnixNano": _nanos(step.occurred_at), "endTimeUnixNano": _nanos(end or step.occurred_at),
        "attributes": _attrs({"axyntro.step.type": step.type, "axyntro.step.target": step.target,
                              "axyntro.step.seq": step.seq, "axyntro.step.attempt": step.attempt,
                              "axyntro.step.denied": bool(body.get("denied")) or None,
                              "axyntro.step.tokens": body.get("tokens"), "axyntro.step.cost_usd": body.get("cost_usd")}),
        "status": _status_code(step.type == "TOOL_FAILED", 0),
    }


def traces(deployment: AgentDeploymentModel, execution: AgentExecutionModel,
           steps: list[ExecutionStepModel]) -> dict[str, Any]:
    trace_id = _id(f"trace:{execution.id}", 32)
    root_id = _id(f"span:{execution.id}", 16)
    end = execution.completed_at or execution.started_at
    spans = [{
        "traceId": trace_id, "spanId": root_id, "name": "agent.execution", "kind": 2,
        "startTimeUnixNano": _nanos(execution.started_at or execution.created_at), "endTimeUnixNano": _nanos(end),
        "attributes": _attrs({"axyntro.execution": execution.id, "axyntro.status": execution.status,
                              "axyntro.trigger": execution.trigger, "axyntro.client": execution.client_id,
                              "axyntro.shadow": execution.shadow_of is not None, "axyntro.attempt": execution.attempt,
                              "axyntro.cost_usd": execution.total_cost_usd, "axyntro.tokens": execution.total_tokens}),
        "status": _status_code(execution.status in ("FAILED", "TIMED_OUT"), 1),
    }]
    ends = [s.occurred_at for s in steps[1:]] + [end]
    spans += [_step_span(trace_id, root_id, step, step_end) for step, step_end in zip(steps, ends)]
    return {"resourceSpans": [{"resource": _resource(deployment, execution),
                               "scopeSpans": [{"scope": {"name": SERVICE_NAME}, "spans": spans}]}]}


def metrics(deployment: AgentDeploymentModel, execution: AgentExecutionModel) -> dict[str, Any]:
    start, end = _nanos(execution.started_at or execution.created_at), _nanos(execution.completed_at)
    labels = _attrs({"axyntro.status": execution.status, "axyntro.shadow": execution.shadow_of is not None})

    def point(value: float | int) -> dict[str, Any]:
        key = "asInt" if isinstance(value, int) else "asDouble"
        return {"startTimeUnixNano": start, "timeUnixNano": end, key: str(value) if key == "asInt" else value,
                "attributes": labels}

    def total(name: str, unit: str, value: float | int) -> dict[str, Any]:
        return {"name": name, "unit": unit,
                "sum": {"dataPoints": [point(value)], "aggregationTemporality": 1, "isMonotonic": True}}

    duration = ((execution.completed_at - (execution.started_at or execution.created_at)).total_seconds()
                if execution.completed_at else 0.0)
    items = [total("axyntro.runtime.executions", "1", 1), total("axyntro.runtime.tokens", "1", execution.total_tokens or 0),
             {"name": "axyntro.runtime.duration", "unit": "s", "gauge": {"dataPoints": [point(float(duration))]}}]
    if execution.total_cost_usd is not None:
        items.append(total("axyntro.runtime.cost", "USD", float(execution.total_cost_usd)))
    return {"resourceMetrics": [{"resource": _resource(deployment, execution),
                                 "scopeMetrics": [{"scope": {"name": SERVICE_NAME}, "metrics": items}]}]}


def _headers(telemetry: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for name, ref in (telemetry.get("headers") or {}).items():
        value = secret_store.resolve(ref, current_slug())
        if value is None:
            raise RuntimeError(f"The telemetry header '{name}' names a secret that isn't set")
        headers[name] = value
    return headers


@handler(JOB_KIND)
async def runtime_telemetry(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    execution = session.get(AgentExecutionModel, payload["execution_id"])
    if execution is None:
        return {"skipped": True}
    deployment = session.get(AgentDeploymentModel, execution.deployment_id)
    telemetry = deployment.telemetry or {}
    endpoint = (telemetry.get("otlp_endpoint") or "").rstrip("/")
    if not endpoint:
        return {"skipped": True}
    check_production_url(endpoint)
    steps = list(session.scalars(select(ExecutionStepModel).where(ExecutionStepModel.execution_id == execution.id)
                                 .order_by(ExecutionStepModel.attempt, ExecutionStepModel.seq)))
    headers = _headers(telemetry)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        for path, body in (("/v1/traces", traces(deployment, execution, steps)),
                           ("/v1/metrics", metrics(deployment, execution))):
            response = await client.post(endpoint + path, json=body, headers=headers)
            if response.status_code >= 300:
                raise RuntimeError(f"The collector answered {response.status_code} on {path}")
    return {"spans": len(steps) + 1}
