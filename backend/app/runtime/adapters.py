"""Epic 39, US39.6: production adapters -- the ToolPort that calls real
systems.

A deployment binds each tool contract to the catalog application's
endpoint tagged `production` and an allow-list of the operations the twin
tested (the OpenAPI-shaped tool: an `operation` plus its `params`). The
model sees the tool it was tested with; this module turns its call into
an HTTP request.

The endpoint rule is the twin's, reversed (app/catalog/endpoints.py): a
twin run may never call a production endpoint, and a production adapter
may call nothing else. Checked when a binding is saved and again before
every call. Only https, except hosts listed in `RUNTIME_HTTP_HOSTS` (a
local stand-in of a production system).
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import quote, urlparse

import httpx

from app.config import get_settings
from app.llm import ToolCall
from app.schemas.agents import ToolContract
from app.twin.gateway import changes_data

from .credentials import CredentialError, headers_for
from .executor import EscalationRequired, ExecutionError, ToolResult

_RETRY_STATUSES = {502, 503, 504}


class UnsafeProductionEndpoint(Exception):
    pass


def _http_allowed(host: str) -> bool:
    patterns = [p.strip() for p in get_settings().runtime_http_hosts.split(",") if p.strip()]
    return any(fnmatch.fnmatch(host, p) for p in patterns)


def check_production_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise UnsafeProductionEndpoint("the URL carries credentials; use a binding credential instead")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeProductionEndpoint("the URL has no host")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _http_allowed(host):
        return
    raise UnsafeProductionEndpoint(f"'{url}' isn't https (http is allowed only for RUNTIME_HTTP_HOSTS)")


def check_production_endpoint(url: str, environment: str | None) -> None:
    if environment != "production":
        raise UnsafeProductionEndpoint(
            f"the endpoint is tagged '{environment or 'untagged'}'; production adapters call only endpoints tagged "
            "'production'"
        )
    check_production_url(url)


# The gateway's rule for "changes data", kept under the runtime's name.
is_mutating = changes_data


@dataclass
class Binding:
    system_name: str
    binding_id: str
    base_url: str
    environment: str | None
    operations: list[str]
    api_operations: dict[str, dict[str, str]]
    credential: dict[str, Any]
    contract: ToolContract
    timeout_seconds: float


def _request_parts(binding: Binding, arguments: dict[str, Any]) -> tuple[str, str, dict[str, Any], Any]:
    operation_id = str(arguments.get("operation"))
    operation = binding.api_operations[operation_id]
    params = dict(arguments.get("params") or {})
    path = operation["path"]
    for name in re.findall(r"{([^}]+)}", path):
        if name not in params:
            raise ExecutionError(f"Operation '{operation_id}' needs path parameter '{name}'")
        path = path.replace("{" + name + "}", quote(str(params.pop(name)), safe=""))
    body = params.pop("body", None)
    return operation["method"], binding.base_url.rstrip("/") + path, params, body


def _shape(response: httpx.Response) -> dict[str, Any]:
    """The same response shape the twin's API mode gives the model."""
    try:
        payload = response.json()
    except ValueError:
        payload = {"text": response.text[:2000]}
    if not isinstance(payload, dict):
        payload = {"result": payload}
    if response.status_code >= 400:
        payload = {"error": True, "status_code": response.status_code, **payload}
    return payload


class Circuit(Protocol):
    """US39.9: the deployment's circuit breakers (app/runtime/limits.py)."""

    def blocks(self, system_name: str) -> str | None: ...

    def record(self, system_name: str, *, failed: bool) -> None: ...


class ProductionTools:
    """The ToolPort for a deployment. `on_call` is told about every call
    actually sent (for the audit log), never about its credentials. With
    a `circuit`, a system whose circuit is open isn't called: the case
    escalates. A transport error or a 5xx counts as a failure."""

    def __init__(
        self,
        bindings: dict[str, Binding],
        *,
        execution_id: str,
        on_call: Callable[[Binding, ToolCall, int], Awaitable[None]] | None = None,
        circuit: Circuit | None = None,
    ) -> None:
        self.bindings = bindings
        self.execution_id = execution_id
        self.on_call = on_call
        self.circuit = circuit

    def _binding_for(self, system_name: str, call: ToolCall) -> tuple[Binding, ToolResult | None]:
        """The binding, or a refusal the model sees, before anything is sent."""
        binding = self.bindings.get(system_name)
        if binding is None:
            raise ExecutionError(f"'{system_name}' has no production binding")
        operation_id = call.arguments.get("operation")
        if operation_id not in binding.operations or operation_id not in binding.api_operations:
            reason = f"operation '{operation_id}' isn't allowed on '{system_name}' (allowed: {binding.operations})"
            return binding, ToolResult(response={"error": "operation_not_allowed", "reason": reason}, failed=True,
                                       reason=reason)
        blocked = self.circuit.blocks(system_name) if self.circuit is not None else None
        if blocked:
            raise EscalationRequired(blocked)
        try:
            check_production_endpoint(binding.base_url, binding.environment)
        except UnsafeProductionEndpoint as exc:
            raise ExecutionError(f"Call to '{system_name}' refused: {exc}") from exc
        return binding, None

    def _record(self, system_name: str, *, failed: bool) -> None:
        if self.circuit is not None:
            self.circuit.record(system_name, failed=failed)

    async def call(self, system_name: str, call: ToolCall) -> ToolResult:
        binding, refused = self._binding_for(system_name, call)
        if refused is not None:
            return refused
        method, url, params, body = _request_parts(binding, call.arguments)
        try:
            headers = await headers_for(binding.credential, scope_key=binding.binding_id, action=binding.contract.action)
        except CredentialError as exc:
            raise ExecutionError(f"Can't authenticate to '{system_name}': {exc}") from exc
        if is_mutating(binding.contract):
            # The same key on every retry, so the system applies it once.
            headers["Idempotency-Key"] = f"{self.execution_id}:{call.id}"
        try:
            response = await self._send(binding, method, url, params, body, headers)
        except ExecutionError:
            self._record(system_name, failed=True)
            raise
        self._record(system_name, failed=response.status_code >= 500)
        if self.on_call is not None:
            await self.on_call(binding, call, response.status_code)
        return ToolResult(
            response=_shape(response), failed=response.status_code >= 400,
            reason=f"{system_name} answered {response.status_code}" if response.status_code >= 400 else None,
        )

    async def _send(self, binding: Binding, method: str, url: str, params: dict[str, Any], body: Any,
                    headers: dict[str, str]) -> httpx.Response:
        attempts = 1 + binding.contract.retry_max_attempts
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=binding.timeout_seconds, follow_redirects=False) as client:
            for attempt in range(attempts):
                if attempt:
                    await asyncio.sleep(min(0.5 * 2 ** (attempt - 1), 5))
                try:
                    response = await client.request(method, url, params=params or None, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = exc
                    continue
                if response.status_code in _RETRY_STATUSES and attempt + 1 < attempts:
                    continue
                return response
        raise ExecutionError(f"Call to '{binding.system_name}' failed: {type(last_error).__name__}")
