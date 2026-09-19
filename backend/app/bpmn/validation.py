"""US3.4: structural validation against the bpmn-authoring skill's
checklist. This is NOT full BPMN 2.0 XSD validation -- vendoring and
correctly wiring OMG's multi-file XSD bundle (BPMN20/Semantic/BPMNDI/DI/DC)
is real additional scope, deliberately deferred; see this epic's "Known
gaps". This checks the specific structural properties the skill calls out:
well-formedness, dangling refs, orphan nodes, DI consistency, duplicate
ids. Gateway fork/join pairing (the skill's other checklist item) is also
deferred -- see app/bpmn/mapping.py's module docstring on why joins aren't
synthesized.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .builder import _tag

_FLOW_NODE_TAGS = {
    "task",
    "userTask",
    "serviceTask",
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
    "startEvent",
    "endEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
}


def _validate(xml_str: str) -> tuple[list[str], list[str]]:
    """Returns (structural_issues, integrity_issues). Structural = content
    completeness ("no incoming/outgoing flow") -- Epic 11 owns detecting
    and resolving these against the ProcessSchema now (app/gap_analysis/),
    so Finalize (app/api/versions.py) no longer blocks on them directly.
    Integrity = is the generated XML itself well-formed and renderable
    (malformed XML, duplicate/dangling ids, missing DI) -- a different,
    lower-level concern that should never legitimately fail if
    app/bpmn/builder.py is correct, and stays Finalize's deterministic
    backstop (validate_bpmn_integrity)."""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        return [], [f"Not well-formed XML: {exc}"]

    structural_issues: list[str] = []
    integrity_issues: list[str] = []

    process = root.find(_tag("bpmn", "process"))
    if process is None:
        return [], ["No <bpmn:process> element found"]

    node_ids: list[str] = []
    incoming_count: dict[str, int] = {}
    outgoing_count: dict[str, int] = {}
    for el in process:
        local = el.tag.split("}", 1)[-1]
        if local not in _FLOW_NODE_TAGS:
            continue
        node_id = el.get("id")
        if node_id:
            node_ids.append(node_id)
            incoming_count.setdefault(node_id, 0)
            outgoing_count.setdefault(node_id, 0)

    duplicates = {node_id for node_id in node_ids if node_ids.count(node_id) > 1}
    if duplicates:
        integrity_issues.append(f"Duplicate element ids: {sorted(duplicates)}")

    node_id_set = set(node_ids)
    flow_ids: list[str] = []
    for flow in process.findall(_tag("bpmn", "sequenceFlow")):
        flow_id = flow.get("id")
        if flow_id:
            flow_ids.append(flow_id)
        source_ref = flow.get("sourceRef")
        target_ref = flow.get("targetRef")
        if source_ref not in node_id_set:
            integrity_issues.append(f"sequenceFlow '{flow_id}' sourceRef '{source_ref}' does not exist")
        else:
            outgoing_count[source_ref] = outgoing_count.get(source_ref, 0) + 1
        if target_ref not in node_id_set:
            integrity_issues.append(f"sequenceFlow '{flow_id}' targetRef '{target_ref}' does not exist")
        else:
            incoming_count[target_ref] = incoming_count.get(target_ref, 0) + 1

    duplicate_flow_ids = {fid for fid in flow_ids if flow_ids.count(fid) > 1}
    if duplicate_flow_ids:
        integrity_issues.append(f"Duplicate flow ids: {sorted(duplicate_flow_ids)}")

    lane_ids = {
        lane.get("id")
        for lane_set in process.findall(_tag("bpmn", "laneSet"))
        for lane in lane_set.findall(_tag("bpmn", "lane"))
        if lane.get("id")
    }

    for el in process:
        local = el.tag.split("}", 1)[-1]
        node_id = el.get("id")
        if local not in _FLOW_NODE_TAGS or not node_id:
            continue
        if local != "startEvent" and incoming_count.get(node_id, 0) == 0:
            structural_issues.append(f"'{node_id}' ({local}) has no incoming flow and is not a start event")
        if local != "endEvent" and outgoing_count.get(node_id, 0) == 0:
            structural_issues.append(f"'{node_id}' ({local}) has no outgoing flow and is not an end event")

    diagram = root.find(_tag("bpmndi", "BPMNDiagram"))
    if diagram is None:
        integrity_issues.append("No <bpmndi:BPMNDiagram> -- diagram will not render (DI is mandatory)")
    else:
        shape_refs = {
            shape.get("bpmnElement")
            for shape in diagram.iter(_tag("bpmndi", "BPMNShape"))
            if shape.get("bpmnElement")
        }
        edge_refs = {
            edge.get("bpmnElement") for edge in diagram.iter(_tag("bpmndi", "BPMNEdge")) if edge.get("bpmnElement")
        }
        missing_shapes = node_id_set - shape_refs
        if missing_shapes:
            integrity_issues.append(f"Elements with no DI shape: {sorted(missing_shapes)}")
        # Lanes need their own DI shape to render as a visible swimlane band
        # at all -- bpmn-js silently skips drawing a lane with no matching
        # BPMNShape even though its semantic flowNodeRefs are otherwise
        # complete (see app/bpmn/layout.py's module docstring). Checked
        # separately from missing_shapes since a lane id is never a flow
        # node id -- also why lane ids must be excluded from the dangling
        # -shapes check below, not just added to it.
        missing_lane_shapes = lane_ids - shape_refs
        if missing_lane_shapes:
            integrity_issues.append(
                f"Lanes with no DI shape (will not render as a swimlane): {sorted(missing_lane_shapes)}"
            )
        dangling_shapes = shape_refs - node_id_set - lane_ids
        if dangling_shapes:
            integrity_issues.append(f"DI shapes referencing elements that don't exist: {sorted(dangling_shapes)}")
        missing_edges = set(flow_ids) - edge_refs
        if missing_edges:
            integrity_issues.append(f"Flows with no DI edge: {sorted(missing_edges)}")
        dangling_edges = edge_refs - set(flow_ids)
        if dangling_edges:
            integrity_issues.append(f"DI edges referencing flows that don't exist: {sorted(dangling_edges)}")

    return structural_issues, integrity_issues


def validate_bpmn(xml_str: str) -> list[str]:
    """Returns a list of human-readable issues (structural + integrity
    combined); empty list means valid. Unchanged behavior/callers from
    before the Epic 11 split -- app/api/bpmn.py's advisory display and
    app/api/chat.py's regression-count check both still want the full
    combined list."""
    structural_issues, integrity_issues = _validate(xml_str)
    return structural_issues + integrity_issues


def validate_bpmn_integrity(xml_str: str) -> list[str]:
    """Just the XML/DI-integrity subset (see _validate's docstring) --
    Finalize's deterministic backstop (app/api/versions.py) now that
    content-completeness is Epic 11's gap-analysis's job instead."""
    _structural_issues, integrity_issues = _validate(xml_str)
    return integrity_issues
