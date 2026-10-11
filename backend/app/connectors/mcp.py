"""Epic 31, US31.2: MCP as the standard connector interface.

A customer's own team can expose any system of record as an MCP server and
point a connector at it -- no fork of the product needed. This is a
minimal MCP client over Streamable HTTP (JSON-RPC 2.0): `initialize`, then
`tools/call` of one configured, read-only tool. The tool's result (JSON in
a text content block, or `structuredContent`) is a list of records, mapped
to context entities by the connector's `mapping`.

Config:
    url             MCP endpoint (Streamable HTTP)
    tool            tool to call (must be a read tool -- see `_READ_ONLY`)
    arguments       tool arguments (optional)
    kind            context entity kind the records become
    mapping         {"external_id": "<field>", "name": "<field>", "code": "<field>",
                     "data": {"<entity data key>": "<record field>", ...}}
    token_env       env var holding a bearer token (optional)
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings

from .base import Connector, ConnectorError, SyncBatch
from .mapping import map_records

_PROTOCOL_VERSION = "2025-06-18"
# Refuse tools whose name says they change something -- connectors are
# read-only by construction, not just by convention.
_WRITE_WORDS = ("create", "update", "delete", "write", "set", "post", "put", "patch", "remove", "send", "approve")


def _read_only(tool: str) -> bool:
    lowered = tool.lower()
    return not any(lowered.startswith(w) or f"_{w}" in lowered or f".{w}" in lowered for w in _WRITE_WORDS)


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    """Streamable HTTP may answer with JSON or a single-event SSE stream."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise ConnectorError("MCP server sent an empty event stream")
    return response.json()


class McpConnector(Connector):
    type = "mcp"
    description = "Any system exposed as an MCP server (read-only tool)"

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: dict[str, Any], rpc_id: int, headers: dict[str, str]) -> dict[str, Any]:
        response = await client.post(
            self.config["url"],
            json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
            headers=headers,
        )
        if response.status_code >= 400:
            raise ConnectorError(f"MCP {method} failed: HTTP {response.status_code}")
        message = _parse_response(response)
        if "error" in message:
            raise ConnectorError(f"MCP {method} error: {message['error'].get('message', message['error'])}")
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id
        return message.get("result", {})

    async def fetch(self) -> SyncBatch:
        for key in ("url", "tool", "kind"):
            if not self.config.get(key):
                raise ConnectorError(f"MCP connector needs '{key}'")
        tool = self.config["tool"]
        if not _read_only(tool):
            raise ConnectorError(f"Tool '{tool}' looks like it changes data -- connectors only call read tools")
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        token = self.secret()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=get_settings().connector_timeout_seconds) as client:
            await self._rpc(
                client,
                "initialize",
                {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "axyntro", "version": "1"}},
                1,
                headers,
            )
            await client.post(self.config["url"], json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
            result = await self._rpc(
                client, "tools/call", {"name": tool, "arguments": self.config.get("arguments") or {}}, 2, headers
            )
        if result.get("isError"):
            raise ConnectorError(f"MCP tool '{tool}' reported an error")
        records: Any = result.get("structuredContent")
        if records is None:
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            try:
                records = json.loads("".join(texts)) if texts else []
            except json.JSONDecodeError as exc:
                raise ConnectorError(f"MCP tool '{tool}' did not return JSON records") from exc
        if isinstance(records, dict):
            records = records.get(self.config.get("records_key", "items"), records.get("results", []))
        if not isinstance(records, list):
            raise ConnectorError(f"MCP tool '{tool}' did not return a list of records")
        return SyncBatch(items=map_records(records, self.config["kind"], self.config.get("mapping") or {}))
