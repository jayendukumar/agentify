"""US3.2: map the canonical ProcessSchema (already extracted by Epic 1/2 --
no new LLM call here, see app/bpmn/builder.py's module docstring) onto BPMN
2.0 constructs, per the bpmn-authoring skill's mapping table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.common import ProcessSchema


@dataclass
class BpmnNode:
    id: str
    bpmn_type: str  # e.g. "task", "userTask", "exclusiveGateway", "startEvent"
    label: str
    lane_id: str | None
    source_element_id: str
    low_confidence: bool


@dataclass
class BpmnFlow:
    id: str
    source_ref: str  # a BpmnNode.id
    target_ref: str
    condition: str | None


@dataclass
class BpmnLane:
    id: str
    name: str


@dataclass
class BpmnModel:
    process_id: str
    process_name: str
    lanes: list[BpmnLane]
    nodes: list[BpmnNode]
    flows: list[BpmnFlow]

    @property
    def low_confidence_node_ids(self) -> list[str]:
        return [n.id for n in self.nodes if n.low_confidence]


def _gateway_type(outgoing_conditions: list[str | None]) -> str:
    """Skill's rule: exclusive if every branch has a distinct condition,
    parallel if none do (all branches always taken), inclusive if mixed.
    With no outgoing flows at all (nothing to infer from), default to the
    safest/most common case."""
    if not outgoing_conditions:
        return "exclusiveGateway"
    has_condition = [c is not None for c in outgoing_conditions]
    if all(has_condition):
        return "exclusiveGateway"
    if not any(has_condition):
        return "parallelGateway"
    return "inclusiveGateway"


def _task_type(actor_type: str | None) -> str:
    if actor_type == "role":
        return "userTask"
    if actor_type == "system":
        return "serviceTask"
    return "task"


def map_schema_to_bpmn(process_id: str, schema: ProcessSchema) -> BpmnModel:
    """Every actor becomes a lane in a single pool (see this module's
    docstring in the epic doc / skill for why pools/message-flows for
    external_party actors aren't implemented yet -- known gap, not an
    oversight).
    """
    actor_by_id = {actor.id: actor for actor in schema.actors}

    outgoing_conditions: dict[str, list[str | None]] = {}
    for flow in schema.flows:
        outgoing_conditions.setdefault(flow.from_, []).append(flow.condition)

    nodes: list[BpmnNode] = []
    for element in schema.elements:
        actor = actor_by_id.get(element.actor_id) if element.actor_id else None
        lane_id = f"Lane_{actor.id}" if actor else None

        if element.type == "start_event":
            bpmn_type = "startEvent"
            prefix = "Event"
        elif element.type == "end_event":
            bpmn_type = "endEvent"
            prefix = "Event"
        elif element.type == "intermediate_event":
            # Defaulting to catch (waiting on something) -- the schema
            # doesn't currently distinguish catch vs throw semantics; see
            # the bpmn-authoring skill and this epic's "Known gaps".
            bpmn_type = "intermediateCatchEvent"
            prefix = "Event"
        elif element.type == "decision":
            bpmn_type = _gateway_type(outgoing_conditions.get(element.id, []))
            prefix = "Gateway"
        else:  # "task"
            bpmn_type = _task_type(actor.type if actor else None)
            prefix = "Task"

        nodes.append(
            BpmnNode(
                id=f"{prefix}_{element.id}",
                bpmn_type=bpmn_type,
                label=element.label,
                lane_id=lane_id,
                source_element_id=element.id,
                low_confidence=element.confidence != "high",
            )
        )

    node_id_by_element_id = {n.source_element_id: n.id for n in nodes}
    flows: list[BpmnFlow] = []
    for flow in schema.flows:
        source_ref = node_id_by_element_id.get(flow.from_)
        target_ref = node_id_by_element_id.get(flow.to)
        if source_ref is None or target_ref is None:
            # A flow referencing an element that doesn't exist in this
            # schema (shouldn't happen with well-formed extraction output,
            # but the LLM's output is never fully trusted elsewhere in this
            # codebase either -- skip rather than emit a dangling reference
            # that would fail US3.4 validation).
            continue
        flows.append(BpmnFlow(id=f"Flow_{flow.id}", source_ref=source_ref, target_ref=target_ref, condition=flow.condition))

    lanes = [BpmnLane(id=f"Lane_{actor.id}", name=actor.name) for actor in schema.actors]

    return BpmnModel(process_id=process_id, process_name=schema.process_name, lanes=lanes, nodes=nodes, flows=flows)
