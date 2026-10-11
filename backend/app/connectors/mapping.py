"""Epic 31: map source records (dicts) onto SyncItems with a small,
declarative field mapping, shared by the MCP and HTTP connectors.

    {"external_id": "sys_id", "name": "name", "code": "number",
     "data": {"vendor": "vendor.name", "aliases": "aliases"},
     "applies_to": {"jurisdictions": "regions"}}

Dotted paths read nested fields; a comma/semicolon-separated string mapped
to a list field (aliases, synonyms, jurisdictions) is split.
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.context import AppliesTo

from .base import ConnectorError, SyncItem

_LIST_FIELDS = {"aliases", "synonyms", "required_guardrails", "residency", "data_classes", "data_assets"}


def _get(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in re.split(r"[;,]", str(value)) if v.strip()]


def map_records(records: list[dict[str, Any]], kind: str, mapping: dict[str, Any]) -> list[SyncItem]:
    id_field = mapping.get("external_id", "id")
    name_field = mapping.get("name", "name")
    items = []
    for record in records:
        if not isinstance(record, dict):
            continue
        external_id = _get(record, id_field)
        name = _get(record, name_field)
        if external_id in (None, "") or not name:
            continue
        data: dict[str, Any] = {}
        for key, path in (mapping.get("data") or {}).items():
            value = _get(record, path)
            if value is None:
                continue
            data[key] = _as_list(value) if key in _LIST_FIELDS else value
        scope = {key: _as_list(_get(record, path)) for key, path in (mapping.get("applies_to") or {}).items()}
        try:
            applies_to = AppliesTo.model_validate(scope)
        except ValueError as exc:
            raise ConnectorError(f"Bad applies_to mapping: {exc}") from exc
        code_field = mapping.get("code")
        items.append(
            SyncItem(
                kind=kind,
                external_id=str(external_id),
                name=str(name),
                code=str(_get(record, code_field)) if code_field and _get(record, code_field) is not None else None,
                data=data,
                applies_to=applies_to,
            )
        )
    return items
