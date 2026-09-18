"""Epic 7: parses a finalized diagram's BPMN XML into per-node context for
blueprint evaluation (app/blueprint/service.py). Reads directly off the
version's XML, not the live process schema -- a finalized version is an
immutable snapshot that can already have drifted from the current schema
tables (see app/db/models.py's VersionModel docstring), so the blueprint
must be evaluated against exactly what was finalized, not whatever the
schema happens to say today.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

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


@dataclass
class FlowRef:
    node_id: str
    label: str
    condition: str | None


@dataclass
class FlowNodeInfo:
    id: str
    label: str
    bpmn_type: str
    lane_name: str | None
    predecessors: list[FlowRef] = field(default_factory=list)
    successors: list[FlowRef] = field(default_factory=list)


def extract_flow_nodes(xml_str: str) -> list[FlowNodeInfo]:
    """Returns one FlowNodeInfo per flow node (task/gateway/event) in
    document order, empty list if the XML has no <bpmn:process>."""
    root = ET.fromstring(xml_str)
    process = root.find(_tag("bpmn", "process"))
    if process is None:
        return []

    node_elements: dict[str, ET.Element] = {}
    for el in process:
        local = el.tag.split("}", 1)[-1]
        node_id = el.get("id")
        if local in _FLOW_NODE_TAGS and node_id:
            node_elements[node_id] = el

    label_by_id = {node_id: (el.get("name") or node_id) for node_id, el in node_elements.items()}

    lane_by_node: dict[str, str] = {}
    for lane_set in process.findall(_tag("bpmn", "laneSet")):
        for lane in lane_set.findall(_tag("bpmn", "lane")):
            lane_name = lane.get("name") or lane.get("id") or ""
            for ref in lane.findall(_tag("bpmn", "flowNodeRef")):
                if ref.text:
                    lane_by_node[ref.text.strip()] = lane_name

    predecessors: dict[str, list[FlowRef]] = {node_id: [] for node_id in node_elements}
    successors: dict[str, list[FlowRef]] = {node_id: [] for node_id in node_elements}
    for flow in process.findall(_tag("bpmn", "sequenceFlow")):
        source = flow.get("sourceRef")
        target = flow.get("targetRef")
        if source not in node_elements or target not in node_elements:
            continue
        condition_el = flow.find(_tag("bpmn", "conditionExpression"))
        condition = condition_el.text.strip() if condition_el is not None and condition_el.text else None
        successors[source].append(FlowRef(node_id=target, label=label_by_id[target], condition=condition))
        predecessors[target].append(FlowRef(node_id=source, label=label_by_id[source], condition=condition))

    return [
        FlowNodeInfo(
            id=node_id,
            label=label_by_id[node_id],
            bpmn_type=el.tag.split("}", 1)[-1],
            lane_name=lane_by_node.get(node_id),
            predecessors=predecessors[node_id],
            successors=successors[node_id],
        )
        for node_id, el in node_elements.items()
    ]
