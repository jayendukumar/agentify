"""Epic 36, US36.1: simulated systems answered from sample records.

When a twin environment carries `system_data` for a system, an agent's
calls to that system are answered here -- deterministically, with no LLM
call -- instead of by Proxy mode inventing a response. The agent's tool is
generated from the data too (an `action` plus the lookup fields), so it
needs no LLM schema inference either.

Per run, writes (create/update) go to a copy of the records, so a later
lookup in the same run sees them ("was this invoice already posted?"),
and the environment itself is never mutated.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.oracles.rules import _eq
from app.schemas.environment import SimulatedSystemData, TwinEnvironment
from app.schemas.twin import InferredToolSchema

_MAX_RESULTS = 5
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(name: str) -> set[str]:
    return set(_TOKEN.findall(name.lower()))


def find_system_data(environment: TwinEnvironment | None, system_name: str) -> SimulatedSystemData | None:
    """Exact name, then an alias, then a name whose words all appear in the
    agent's system name ("Vendor Master" serves "Vendor Master (SAP MDG)")."""
    if environment is None or not environment.system_data:
        return None
    if system_name in environment.system_data:
        return environment.system_data[system_name]
    wanted = system_name.strip().lower()
    for data in environment.system_data.values():
        if any(alias.strip().lower() == wanted for alias in data.aliases):
            return data
    words = _tokens(system_name)
    candidates = [
        (len(_tokens(key)), data)
        for key, data in environment.system_data.items()
        if _tokens(key) and _tokens(key) <= words
    ]
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


def _tool_name(system_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", system_name).strip("_").lower() or "system"
    return f"{slug[:56]}_records"


def records_tool_schema(system_name: str, data: SimulatedSystemData) -> InferredToolSchema:
    properties: dict[str, Any] = {
        "action": {
            "type": "string",
            "enum": list(data.operations),
            "description": "lookup: find records; create: add a record; update: change a found record.",
        }
    }
    for field in data.lookup_fields:
        properties[field] = {"type": "string", "description": f"Find records whose {field} equals this value."}
    if {"create", "update"} & set(data.operations):
        properties["data"] = {"type": "object", "description": "The fields to write (create/update)."}
    sample_fields = sorted({k for r in data.records[:20] for k in r})
    return InferredToolSchema(
        system_name=system_name,
        tool_name=_tool_name(system_name),
        description=(
            f"{data.description or system_name}. Look up records by any of: {', '.join(data.lookup_fields) or 'any field'}"
            + (f"; or {', '.join(op for op in data.operations if op != 'lookup')} a record." if len(data.operations) > 1 else ".")
        ),
        parameters={"type": "object", "properties": properties, "required": ["action"]},
        response_shape_description=(
            f'{{"found": bool, "count": int, "records": [{{{", ".join(sample_fields[:12])}}}]}} for lookup; '
            '{"status": "accepted", "reference": str} for writes'
        ),
        source="records",
    )


class RecordsSession:
    """One run's view of every sample-data system: lazily copied on first
    write, so lookups after a write see it."""

    def __init__(self) -> None:
        self._records: dict[int, list[dict[str, Any]]] = {}
        self._writes: dict[int, int] = {}

    def _rows(self, data: SimulatedSystemData) -> list[dict[str, Any]]:
        return self._records.get(id(data), data.records)

    def _writable(self, data: SimulatedSystemData) -> list[dict[str, Any]]:
        if id(data) not in self._records:
            self._records[id(data)] = copy.deepcopy(data.records)
        return self._records[id(data)]

    def call(self, data: SimulatedSystemData, system_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        action = str(arguments.get("action") or "lookup").lower()
        if action not in data.operations:
            return {"error": "operation_not_supported", "message": f"{system_name} does not support '{action}'"}
        criteria = {k: v for k, v in arguments.items()
                    if k not in ("action", "data") and v not in (None, "") and not isinstance(v, (dict, list))}
        if action == "create":
            return self._create(data, arguments.get("data") or criteria)
        matches = self._match(data, criteria)
        if action == "update":
            return self._update(data, system_name, matches, arguments.get("data") or {})
        if not criteria:
            return {"found": False, "count": 0, "records": [],
                    "message": f"Give at least one of {', '.join(data.lookup_fields) or 'a field'} to look up {system_name}"}
        rows = [self._rows(data)[i] for _, i in matches[:_MAX_RESULTS]]
        if not rows:
            return {"found": False, "count": 0, "records": [], "message": f"No matching record in {system_name}"}
        return {"found": True, "count": len(matches), "records": rows}

    def _match(self, data: SimulatedSystemData, criteria: dict[str, Any]) -> list[tuple[int, int]]:
        """All provided lookup fields must match (AND). Arguments that aren't
        lookup fields but are record fields count too; anything else (an
        agent's free-text `reason`, say) is ignored rather than failing the
        lookup."""
        rows = self._rows(data)
        known = {k for r in rows[:50] for k in r}
        usable = {k: v for k, v in criteria.items() if k in data.lookup_fields or k in known}
        if not usable:
            # The agent named the field differently ("vendor" for
            # "supplier_id"): match the value against any lookup field.
            values = list(criteria.values())
            return [(1, i) for i, r in enumerate(rows)
                    if any(_eq(r.get(f), v) for f in data.lookup_fields for v in values if r.get(f) is not None)]
        return [(len(usable), i) for i, r in enumerate(rows)
                if all(r.get(k) is not None and _eq(r.get(k), v) for k, v in usable.items())]

    def _update(self, data: SimulatedSystemData, system_name: str, matches: list[tuple[int, int]], changes: dict[str, Any]) -> dict[str, Any]:
        """Changes the first matching record, in this run's copy only."""
        if not matches:
            return {"status": "rejected", "message": f"No matching record in {system_name} to update"}
        row = self._writable(data)[matches[0][1]]
        row.update(changes)
        return {"status": "accepted", "reference": self._reference(data), "record": row}

    def _reference(self, data: SimulatedSystemData) -> str:
        self._writes[id(data)] = self._writes.get(id(data), 0) + 1
        return f"{data.reference_prefix}-{self._writes[id(data)]:05d}"

    def _create(self, data: SimulatedSystemData, record: Any) -> dict[str, Any]:
        record = dict(record) if isinstance(record, dict) else {"value": record}
        reference = self._reference(data)
        self._writable(data).append({**record, "reference": reference})
        return {"status": "accepted", "reference": reference, "record": record}
