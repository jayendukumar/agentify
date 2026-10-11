"""Epic 29, US29.4: parse an OpenAPI (3.x, or Swagger 2.0) document into
the operations a twin tool schema needs, and build that tool schema.

The twin's model is one callable tool per system (app/twin/
schema_inference.py), so a spec becomes one tool whose arguments are
`{"operation": <operationId>, "params": {...}}`, with every operation and
its parameters described. That keeps the engine unchanged while the agent
sees the real operations and the simulator sees the real response shapes.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from app.schemas.context import ApiOperation
from app.schemas.twin import InferredToolSchema

_METHODS = ("get", "post", "put", "patch", "delete")
_MAX_OPERATIONS = 40


class OpenApiError(Exception):
    pass


def load_spec(raw: str | bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        try:
            spec = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise OpenApiError(f"Not valid JSON or YAML: {exc}") from exc
    if not isinstance(spec, dict) or not ("openapi" in spec or "swagger" in spec):
        raise OpenApiError("Not an OpenAPI document (no 'openapi' or 'swagger' version field)")
    if not isinstance(spec.get("paths"), dict) or not spec["paths"]:
        raise OpenApiError("The OpenAPI document has no paths")
    return spec


def _resolve(spec: dict[str, Any], node: Any, refs: int = 0) -> Any:
    """Inline local `$ref`s. Only reference hops are bounded (so recursive
    schemas stop); ordinary nesting depth is not."""
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
            if refs >= 4:
                return {}
            target: Any = spec
            for part in node["$ref"][2:].split("/"):
                target = target.get(part, {}) if isinstance(target, dict) else {}
            return _resolve(spec, target, refs + 1)
        return {k: _resolve(spec, v, refs) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(spec, v, refs) for v in node]
    return node


def _operation_id(method: str, path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", f"{method}_{path}").strip("_").lower()


def parse_operations(spec: dict[str, Any]) -> list[ApiOperation]:
    operations: list[ApiOperation] = []
    for path, item in spec["paths"].items():
        if not isinstance(item, dict):
            continue
        shared_params = item.get("parameters", [])
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            properties: dict[str, Any] = {}
            required: list[str] = []
            for param in [*shared_params, *op.get("parameters", [])]:
                param = _resolve(spec, param)
                if not isinstance(param, dict) or "name" not in param or param.get("in") == "header":
                    continue
                properties[param["name"]] = param.get("schema") or {"type": param.get("type", "string")}
                if param.get("required"):
                    required.append(param["name"])
            body = _resolve(spec, op.get("requestBody", {})).get("content", {}).get("application/json", {}).get("schema")
            if isinstance(body, dict):
                properties["body"] = body
                required.append("body")
            responses = op.get("responses", {}) or {}
            ok = next((responses[c] for c in ("200", "201", "202") if c in responses), {})
            ok = _resolve(spec, ok)
            response_schema = ok.get("content", {}).get("application/json", {}).get("schema") or ok.get("schema") or {}
            operations.append(
                ApiOperation(
                    operation_id=op.get("operationId") or _operation_id(method, path),
                    method=method.upper(),
                    path=path,
                    summary=op.get("summary") or op.get("description", "")[:200],
                    parameters={"type": "object", "properties": properties, "required": required},
                    response_schema=response_schema if isinstance(response_schema, dict) else {},
                )
            )
            if len(operations) >= _MAX_OPERATIONS:
                return operations
    return operations


def _tool_name(system_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", system_name).strip("_").lower()[:48] or "system"


def _shape(schema: dict[str, Any]) -> str:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if isinstance(props, dict) and props:
        return "{" + ", ".join(f"{k}: {v.get('type', 'object') if isinstance(v, dict) else 'value'}" for k, v in list(props.items())[:12]) + "}"
    if isinstance(schema, dict) and schema.get("type") == "array":
        return f"[{_shape(schema.get('items', {}))}]"
    return json.dumps(schema)[:200] if schema else "a JSON object"


def build_tool_schema(
    system_name: str,
    application_id: str,
    operations: list[ApiOperation],
    *,
    base_url: str | None = None,
    environment: str | None = None,
) -> InferredToolSchema:
    lines = [f"Call the {system_name} API. Choose one operation and pass its parameters in 'params':"]
    for op in operations:
        params = ", ".join(op.parameters.get("properties", {}).keys()) or "no parameters"
        lines.append(f"- {op.operation_id}: {op.method} {op.path} -- {op.summary or 'no summary'} (params: {params})")
    responses = "; ".join(f"{op.operation_id} returns {_shape(op.response_schema)}" for op in operations)
    return InferredToolSchema(
        system_name=system_name,
        tool_name=_tool_name(system_name),
        description="\n".join(lines),
        parameters={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": [op.operation_id for op in operations]},
                "params": {"type": "object", "description": "The chosen operation's parameters."},
            },
            "required": ["operation"],
        },
        response_shape_description=responses,
        source="openapi",
        application_id=application_id,
        api_base_url=base_url,
        api_environment=environment,
        api_operations={op.operation_id: {"method": op.method, "path": op.path} for op in operations},
    )
