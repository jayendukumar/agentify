"""US3.1/US3.3: turn a process's already-extracted ProcessSchema into valid
BPMN 2.0 XML with Diagram Interchange.

US3.1 ("LLM extraction of process structure") is already done -- Epic 1/2's
ingestion pipeline (app/ingestion/structuring.py) already produces the
ProcessSchema (actors, elements, flows) this module reads. There is no new
LLM call here: turning an already-structured schema into BPMN XML is a
pure, deterministic transformation (US3.2's mapping table + US3.3's XML
shape + US3.5's layout), which is faster, free, and fully testable without
mocking an LLM. This is a direct payoff of Epic 1/2's design, not a
shortcut -- if that ever stops being true (e.g. BPMN generation needs
LLM judgment calls XML mapping can't make), revisit this module's approach.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.schemas.common import ProcessSchema

from .layout import compute_layout
from .mapping import BpmnModel, map_schema_to_bpmn

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)


def _tag(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def build_bpmn_xml(process_id: str, schema: ProcessSchema) -> tuple[str, list[str]]:
    """Returns (xml_string, low_confidence_element_ids) -- the latter is
    US3.6: which *source schema* element ids (not BPMN node ids) the
    generated diagram is unsure about, so the UI can flag them."""
    model = map_schema_to_bpmn(process_id, schema)
    layout = compute_layout(model)

    definitions = ET.Element(
        _tag("bpmn", "definitions"),
        {
            "id": f"Definitions_{process_id}",
            "targetNamespace": "http://agentic-solution-generator/bpmn",
        },
    )

    process = ET.SubElement(
        definitions, _tag("bpmn", "process"), {"id": f"Process_{process_id}", "isExecutable": "false"}
    )

    if model.lanes:
        lane_set = ET.SubElement(process, _tag("bpmn", "laneSet"), {"id": f"LaneSet_{process_id}"})
        nodes_by_lane: dict[str, list[str]] = {}
        for node in model.nodes:
            if node.lane_id:
                nodes_by_lane.setdefault(node.lane_id, []).append(node.id)
        for lane in model.lanes:
            lane_el = ET.SubElement(lane_set, _tag("bpmn", "lane"), {"id": lane.id, "name": lane.name})
            for node_id in nodes_by_lane.get(lane.id, []):
                ref = ET.SubElement(lane_el, _tag("bpmn", "flowNodeRef"))
                ref.text = node_id

    for node in model.nodes:
        el = ET.SubElement(process, _tag("bpmn", node.bpmn_type), {"id": node.id, "name": node.label})
        for flow in model.flows:
            if flow.target_ref == node.id:
                ET.SubElement(el, _tag("bpmn", "incoming")).text = flow.id
            if flow.source_ref == node.id:
                ET.SubElement(el, _tag("bpmn", "outgoing")).text = flow.id

    for flow in model.flows:
        attrs = {"id": flow.id, "sourceRef": flow.source_ref, "targetRef": flow.target_ref}
        flow_el = ET.SubElement(process, _tag("bpmn", "sequenceFlow"), attrs)
        if flow.condition:
            expr = ET.SubElement(flow_el, _tag("bpmn", "conditionExpression"))
            expr.text = flow.condition

    diagram = ET.SubElement(definitions, _tag("bpmndi", "BPMNDiagram"), {"id": f"Diagram_{process_id}"})
    plane = ET.SubElement(
        diagram, _tag("bpmndi", "BPMNPlane"), {"id": f"Plane_{process_id}", "bpmnElement": f"Process_{process_id}"}
    )

    for node in model.nodes:
        box = layout[node.id]
        shape = ET.SubElement(
            plane, _tag("bpmndi", "BPMNShape"), {"id": f"Shape_{node.id}", "bpmnElement": node.id}
        )
        ET.SubElement(
            shape,
            _tag("dc", "Bounds"),
            {"x": str(box.x), "y": str(box.y), "width": str(box.width), "height": str(box.height)},
        )

    for flow in model.flows:
        source_box = layout[flow.source_ref]
        target_box = layout[flow.target_ref]
        edge = ET.SubElement(
            plane, _tag("bpmndi", "BPMNEdge"), {"id": f"Edge_{flow.id}", "bpmnElement": flow.id}
        )
        ET.SubElement(edge, _tag("di", "waypoint"), {"x": str(source_box.right), "y": str(source_box.center_y)})
        ET.SubElement(edge, _tag("di", "waypoint"), {"x": str(target_box.left), "y": str(target_box.center_y)})

    xml_bytes = ET.tostring(definitions, encoding="unicode", xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes

    low_confidence_element_ids = [
        node.source_element_id for node in model.nodes if node.low_confidence
    ]
    return xml_str, low_confidence_element_ids
