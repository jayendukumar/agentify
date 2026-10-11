"""Epic 31, US31.3-US31.6: the built-in, standards-based connectors.

- folder: a policy library on disk (a SharePoint/Confluence/OneDrive sync
  folder or an export) -- each file becomes a policy document, ingested
  through Epic 27 when its content changes (US31.3).
- http: a JSON or CSV feed (a CMDB/EA export endpoint, a GRC or data-catalog
  REST API) mapped onto entities (US31.4/US31.5).
- oscal: an OSCAL control catalog (JSON) -- controls straight in (US31.5).
- scim: groups and members from an identity provider over SCIM 2.0, which
  Entra ID and Okta both serve (US31.6).

Vendor-specific connectors (ServiceNow, LeanIX, Collibra, ...) are
configurations of these -- or MCP servers -- rather than code here.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.context.ingestion import SUPPORTED_POLICY_CONTENT_TYPES

from .base import Connector, ConnectorError, SyncBatch, SyncItem
from .mapping import map_records

_EXTENSION_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


class FolderConnector(Connector):
    type = "folder"
    description = "Policy library folder (synced SharePoint/Confluence/OneDrive, or an export)"

    async def fetch(self) -> SyncBatch:
        root = Path(self.config.get("path") or "")
        if not root.is_dir():
            raise ConnectorError(f"Folder '{root}' does not exist or is not a directory")
        items = []
        for path in sorted(root.rglob("*")):
            content_type = _EXTENSION_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
            if not path.is_file() or content_type not in SUPPORTED_POLICY_CONTENT_TYPES:
                continue
            relative = path.relative_to(root).as_posix()
            items.append(
                SyncItem(
                    kind="policy_document",
                    external_id=relative,
                    name=path.stem.replace("_", " ").replace("-", " ").strip(),
                    content=path.read_bytes(),
                    filename=path.name,
                    content_type=content_type,
                )
            )
        return SyncBatch(items=items)


class HttpConnector(Connector):
    type = "http"
    description = "JSON or CSV feed over HTTP (CMDB/EA export, GRC or data-catalog API)"

    async def fetch(self) -> SyncBatch:
        url = self.config.get("url")
        kind = self.config.get("kind")
        if not url or not kind:
            raise ConnectorError("HTTP connector needs 'url' and 'kind'")
        headers = {"Accept": "application/json, text/csv"}
        token = self.secret()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=get_settings().connector_timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise ConnectorError(f"GET {url} failed: HTTP {response.status_code}")
        if "csv" in response.headers.get("content-type", "") or self.config.get("format") == "csv":
            records: Any = list(csv.DictReader(io.StringIO(response.text)))
        else:
            records = response.json()
            for key in (self.config.get("records_key") or "").split("."):
                if key and isinstance(records, dict):
                    records = records.get(key, [])
        if not isinstance(records, list):
            raise ConnectorError("The feed did not return a list of records (set 'records_key')")
        return SyncBatch(items=map_records(records, kind, self.config.get("mapping") or {}))


def _oscal_prose(control: dict[str, Any]) -> str:
    texts = []
    for part in control.get("parts", []) or []:
        if part.get("name") in ("statement", "guidance", "item") and part.get("prose"):
            texts.append(part["prose"])
        for sub in part.get("parts", []) or []:
            if sub.get("prose"):
                texts.append(sub["prose"])
    return " ".join(texts)


def parse_oscal_catalog(document: dict[str, Any]) -> list[SyncItem]:
    catalog = document.get("catalog")
    if not isinstance(catalog, dict):
        raise ConnectorError("Not an OSCAL catalog (no top-level 'catalog')")
    items: list[SyncItem] = []

    def walk_controls(controls: list[dict[str, Any]]) -> None:
        for control in controls or []:
            if control.get("id") and control.get("title"):
                items.append(
                    SyncItem(
                        kind="control",
                        external_id=control["id"],
                        code=control["id"].upper(),
                        name=control["title"],
                        data={"description": _oscal_prose(control), "control_type": "other"},
                    )
                )
            walk_controls(control.get("controls", []))

    def walk_groups(groups: list[dict[str, Any]]) -> None:
        for group in groups or []:
            walk_controls(group.get("controls", []))
            walk_groups(group.get("groups", []))

    walk_controls(catalog.get("controls", []))
    walk_groups(catalog.get("groups", []))
    return items


class OscalConnector(Connector):
    type = "oscal"
    description = "OSCAL control catalog (JSON file path or URL)"

    async def fetch(self) -> SyncBatch:
        source = self.config.get("url") or self.config.get("path")
        if not source:
            raise ConnectorError("OSCAL connector needs 'url' or 'path'")
        if str(source).startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=get_settings().connector_timeout_seconds) as client:
                response = await client.get(source)
            if response.status_code >= 400:
                raise ConnectorError(f"GET {source} failed: HTTP {response.status_code}")
            document = response.json()
        else:
            path = Path(source)
            if not path.is_file():
                raise ConnectorError(f"File '{source}' not found")
            document = json.loads(path.read_text(encoding="utf-8"))
        return SyncBatch(items=parse_oscal_catalog(document))


class ScimConnector(Connector):
    """Groups become roles; each role records its members' display names.
    Only reads (`GET /Groups`, paginated)."""

    type = "scim"
    description = "Identity provider groups over SCIM 2.0 (Entra ID, Okta)"

    async def fetch(self) -> SyncBatch:
        base = (self.config.get("url") or "").rstrip("/")
        if not base:
            raise ConnectorError("SCIM connector needs 'url'")
        headers = {"Accept": "application/scim+json"}
        token = self.secret()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        prefix = self.config.get("group_prefix") or ""
        items: list[SyncItem] = []
        start, page = 1, 100
        async with httpx.AsyncClient(timeout=get_settings().connector_timeout_seconds) as client:
            while True:
                response = await client.get(f"{base}/Groups", params={"startIndex": start, "count": page}, headers=headers)
                if response.status_code >= 400:
                    raise ConnectorError(f"SCIM GET /Groups failed: HTTP {response.status_code}")
                body = response.json()
                resources = body.get("Resources", [])
                for group in resources:
                    name = group.get("displayName") or ""
                    if not group.get("id") or not name or not name.startswith(prefix):
                        continue
                    members = [m.get("display") or m.get("value") for m in group.get("members", []) or []]
                    items.append(
                        SyncItem(
                            kind="role",
                            external_id=group["id"],
                            name=name[len(prefix):].strip() or name,
                            data={"description": f"Identity provider group '{name}'", "members": [m for m in members if m]},
                        )
                    )
                total = int(body.get("totalResults", len(resources)))
                start += len(resources)
                if not resources or start > total:
                    break
        return SyncBatch(items=items)
