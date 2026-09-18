"""Epic 5 / bpmn-chat-ops skill: apply a chat-proposed DiagramDiff to a
ProcessSchema, and translate between bare schema ids (what the LLM and the
diff operate on) and rendered BPMN ids (what the canvas and validate_bpmn
see) -- see app/bpmn/mapping.py's own prefix convention, which this
mirrors rather than duplicates.
"""

from __future__ import annotations

import re
from typing import Literal

from app.ids import new_id
from app.schemas.chat import DiagramDiff, DiagramDiffOperation
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema

_ELEMENT_PREFIXES = {
    "start_event": "Event",
    "end_event": "Event",
    "intermediate_event": "Event",
    "decision": "Gateway",
    "task": "Task",
}


def bpmn_id_for_element(schema_id: str, element_type: str | None) -> str:
    prefix = _ELEMENT_PREFIXES.get(element_type or "task", "Task")
    return f"{prefix}_{schema_id}"


def bpmn_id_for_flow(schema_id: str) -> str:
    return f"Flow_{schema_id}"


def bpmn_id_for_lane(schema_id: str) -> str:
    return f"Lane_{schema_id}"


def strip_bpmn_id(bpmn_id: str) -> tuple[Literal["element", "flow", "lane"], str] | None:
    """Inverse of the bpmn_id_for_* helpers -- mirrors the frontend's
    ElementDetailPanel.tsx classifyBpmnId, now also needed server-side to
    resolve a canvas selection back to a schema id for chat context."""
    for prefix in ("Task_", "Gateway_", "Event_"):
        if bpmn_id.startswith(prefix):
            return "element", bpmn_id[len(prefix) :]
    if bpmn_id.startswith("Flow_"):
        return "flow", bpmn_id[len("Flow_") :]
    if bpmn_id.startswith("Lane_"):
        return "lane", bpmn_id[len("Lane_") :]
    return None


class DiagramDiffError(Exception):
    """Raised when a diff operation references an id that doesn't exist in
    the schema, or is otherwise malformed -- the caller should treat this
    as a 400, not apply anything."""


def _resolve_id(raw_id: str, temp_id_map: dict[str, str]) -> str:
    """A stored diff's element_id/flow_id may be a bare schema id (as the
    LLM emits it), a rendered BPMN id (as app/chat/service.py translates it
    to before persisting, for canvas highlighting), or a same-diff
    temporary id (see apply_diagram_diff) -- accept any of the three so
    callers don't have to know which."""
    if raw_id in temp_id_map:
        return temp_id_map[raw_id]
    stripped = strip_bpmn_id(raw_id)
    return stripped[1] if stripped else raw_id


def _apply_operation(schema: ProcessSchema, op: DiagramDiffOperation, temp_id_map: dict[str, str]) -> None:
    if op.op == "add_element":
        if op.element is None:
            raise DiagramDiffError("add_element operation missing 'element'")
        payload = dict(op.element)
        temp_id = payload.pop("id", None)
        # never trust an LLM-supplied id as the real one -- see
        # structuring.py's same rule; temp_id_map (built in
        # apply_diagram_diff's first pass) already minted the real id this
        # element gets, if it had a temp id to be referenced by.
        payload["id"] = temp_id_map[temp_id] if temp_id in temp_id_map else new_id("el")
        schema.elements.append(ProcessElement.model_validate(payload))

    elif op.op == "remove_element":
        if op.element_id is None:
            raise DiagramDiffError("remove_element operation missing 'element_id'")
        element_id = _resolve_id(op.element_id, temp_id_map)
        before = len(schema.elements)
        schema.elements = [e for e in schema.elements if e.id != element_id]
        if len(schema.elements) == before:
            raise DiagramDiffError(f"remove_element: no element '{op.element_id}'")

    elif op.op == "update_element":
        if op.element_id is None or op.fields is None:
            raise DiagramDiffError("update_element operation missing 'element_id' or 'fields'")
        element_id = _resolve_id(op.element_id, temp_id_map)
        element = next((e for e in schema.elements if e.id == element_id), None)
        if element is None:
            raise DiagramDiffError(f"update_element: no element '{op.element_id}'")
        updated = element.model_copy(update=op.fields)
        schema.elements = [updated if e.id == element_id else e for e in schema.elements]

    elif op.op == "add_flow":
        if op.flow is None:
            raise DiagramDiffError("add_flow operation missing 'flow'")
        payload = dict(op.flow)
        if payload.get("from") is None:
            raise DiagramDiffError("add_flow operation missing 'from'")
        if payload.get("to") is None:
            raise DiagramDiffError("add_flow operation missing 'to'")
        payload["from"] = _resolve_id(payload["from"], temp_id_map)
        payload["to"] = _resolve_id(payload["to"], temp_id_map)
        payload["id"] = new_id("flow")
        schema.flows.append(ProcessFlow.model_validate(payload))

    elif op.op == "remove_flow":
        if op.flow_id is None:
            raise DiagramDiffError("remove_flow operation missing 'flow_id'")
        flow_id = _resolve_id(op.flow_id, temp_id_map)
        before = len(schema.flows)
        schema.flows = [f for f in schema.flows if f.id != flow_id]
        if len(schema.flows) == before:
            raise DiagramDiffError(f"remove_flow: no flow '{op.flow_id}'")

    elif op.op == "update_flow":
        if op.flow_id is None or op.fields is None:
            raise DiagramDiffError("update_flow operation missing 'flow_id' or 'fields'")
        flow_id = _resolve_id(op.flow_id, temp_id_map)
        flow = next((f for f in schema.flows if f.id == flow_id), None)
        if flow is None:
            raise DiagramDiffError(f"update_flow: no flow '{op.flow_id}'")
        fields = dict(op.fields)
        if "from" in fields:
            fields["from_"] = _resolve_id(fields.pop("from"), temp_id_map)
        if "to" in fields:
            fields["to"] = _resolve_id(fields["to"], temp_id_map)
        updated = flow.model_copy(update=fields)
        schema.flows = [updated if f.id == flow_id else f for f in schema.flows]

    else:
        raise DiagramDiffError(f"Unknown operation '{op.op}'")


def apply_diagram_diff(schema: ProcessSchema, diff: DiagramDiff) -> ProcessSchema:
    """Pure function: applies diff.operations in order against a copy of
    schema and returns the result. Reconnection on delete (bpmn-chat-ops
    skill) is not inferred here -- the diff's own operations list already
    includes whatever add_flow/remove_flow ops that requires, so applying
    is mechanical per-op.

    First pass mints a real id for every add_element that carries a
    same-diff temporary id (e.g. "new-1") -- this is how an add_node diff
    (add_element + add_flow wiring it in) lets a later add_flow op in the
    SAME diff reference a node that doesn't exist yet: the LLM has no way
    to know the real minted id in advance, only its own temporary label.
    Discovered via a real LLM call during manual testing (Qwen3.7 Flash
    left the new node's flows with from/to: null without this)."""
    temp_id_map: dict[str, str] = {}
    for op in diff.operations:
        if op.op == "add_element" and op.element and op.element.get("id"):
            temp_id_map[op.element["id"]] = new_id("el")

    updated = schema.model_copy(deep=True)
    for op in diff.operations:
        _apply_operation(updated, op, temp_id_map)
    return updated


_QUOTED_BPMN_ID = re.compile(r"'([A-Za-z]+_[^']+)'")


def humanize_validation_issues(issues: list[str], schema: ProcessSchema) -> list[str]:
    """validate_bpmn's issue strings quote raw BPMN ids (e.g.
    'Task_el_8f055fa669de') -- meaningful to a developer, meaningless to
    the Process Analyst confirming a chat edit. Replaces each quoted id
    with the element/flow/lane's human label from the schema, for display
    in a chat-apply error only; validate_bpmn itself stays id-based since
    PUT /bpmn's manual-edit path has no schema to label against."""
    element_by_id = {e.id: e for e in schema.elements}
    flow_by_id = {f.id: f for f in schema.flows}
    actor_by_id = {a.id: a for a in schema.actors}

    def describe(bpmn_id: str) -> str:
        resolved = strip_bpmn_id(bpmn_id)
        if resolved is None:
            return f"'{bpmn_id}'"
        kind, schema_id = resolved
        if kind == "element":
            element = element_by_id.get(schema_id)
            return f"'{element.label}'" if element else f"'{bpmn_id}'"
        if kind == "flow":
            flow = flow_by_id.get(schema_id)
            if flow is None:
                return f"'{bpmn_id}'"
            from_label = element_by_id[flow.from_].label if flow.from_ in element_by_id else flow.from_
            to_label = element_by_id[flow.to].label if flow.to in element_by_id else flow.to
            return f"the connection from '{from_label}' to '{to_label}'"
        if kind == "lane":
            actor = actor_by_id.get(schema_id)
            return f"'{actor.name}'" if actor else f"'{bpmn_id}'"
        return f"'{bpmn_id}'"

    return [_QUOTED_BPMN_ID.sub(lambda m: describe(m.group(1)), issue) for issue in issues]
