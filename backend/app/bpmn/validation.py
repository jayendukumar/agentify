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


def validate_bpmn(xml_str: str) -> list[str]:
    """Returns a list of human-readable issues; empty list means valid."""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        return [f"Not well-formed XML: {exc}"]

    issues: list[str] = []

    process = root.find(_tag("bpmn", "process"))
    if process is None:
        return ["No <bpmn:process> element found"]

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
        issues.append(f"Duplicate element ids: {sorted(duplicates)}")

    node_id_set = set(node_ids)
    flow_ids: list[str] = []
    for flow in process.findall(_tag("bpmn", "sequenceFlow")):
        flow_id = flow.get("id")
        if flow_id:
            flow_ids.append(flow_id)
        source_ref = flow.get("sourceRef")
        target_ref = flow.get("targetRef")
        if source_ref not in node_id_set:
            issues.append(f"sequenceFlow '{flow_id}' sourceRef '{source_ref}' does not exist")
        else:
            outgoing_count[source_ref] = outgoing_count.get(source_ref, 0) + 1
        if target_ref not in node_id_set:
            issues.append(f"sequenceFlow '{flow_id}' targetRef '{target_ref}' does not exist")
        else:
            incoming_count[target_ref] = incoming_count.get(target_ref, 0) + 1

    duplicate_flow_ids = {fid for fid in flow_ids if flow_ids.count(fid) > 1}
    if duplicate_flow_ids:
        issues.append(f"Duplicate flow ids: {sorted(duplicate_flow_ids)}")

    for el in process:
        local = el.tag.split("}", 1)[-1]
        node_id = el.get("id")
        if local not in _FLOW_NODE_TAGS or not node_id:
            continue
        if local != "startEvent" and incoming_count.get(node_id, 0) == 0:
            issues.append(f"'{node_id}' ({local}) has no incoming flow and is not a start event")
        if local != "endEvent" and outgoing_count.get(node_id, 0) == 0:
            issues.append(f"'{node_id}' ({local}) has no outgoing flow and is not an end event")

    diagram = root.find(_tag("bpmndi", "BPMNDiagram"))
    if diagram is None:
        issues.append("No <bpmndi:BPMNDiagram> -- diagram will not render (DI is mandatory)")
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
            issues.append(f"Elements with no DI shape: {sorted(missing_shapes)}")
        dangling_shapes = shape_refs - node_id_set
        if dangling_shapes:
            issues.append(f"DI shapes referencing elements that don't exist: {sorted(dangling_shapes)}")
        missing_edges = set(flow_ids) - edge_refs
        if missing_edges:
            issues.append(f"Flows with no DI edge: {sorted(missing_edges)}")
        dangling_edges = edge_refs - set(flow_ids)
        if dangling_edges:
            issues.append(f"DI edges referencing flows that don't exist: {sorted(dangling_edges)}")

    return issues
