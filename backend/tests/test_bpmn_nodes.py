from app.bpmn.builder import build_bpmn_xml
from app.bpmn.nodes import extract_flow_nodes
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _schema() -> ProcessSchema:
    return ProcessSchema(
        process_name="P",
        actors=[Actor(id="a1", name="Reviewer", type="role")],
        elements=[
            ProcessElement(id="start", type="start_event", label="Start", source_refs=[_ref()], confidence="high"),
            ProcessElement(
                id="gw", type="decision", label="Approved?", actor_id="a1", source_refs=[_ref()], confidence="high"
            ),
            ProcessElement(
                id="yes", type="task", label="Ship order", actor_id="a1", source_refs=[_ref()], confidence="high"
            ),
            ProcessElement(id="end", type="end_event", label="End", source_refs=[_ref()], confidence="high"),
        ],
        flows=[
            ProcessFlow(id="f1", **{"from": "start"}, to="gw"),
            ProcessFlow(id="f2", **{"from": "gw"}, to="yes", condition="Approved"),
            ProcessFlow(id="f3", **{"from": "yes"}, to="end"),
        ],
    )


def _xml() -> str:
    xml, _ = build_bpmn_xml("proc-1", _schema())
    return xml


def test_extract_flow_nodes_returns_one_entry_per_flow_node():
    nodes = extract_flow_nodes(_xml())
    ids = {n.id for n in nodes}
    assert ids == {"Event_start", "Gateway_gw", "Task_yes", "Event_end"}


def test_extract_flow_nodes_captures_label_type_and_lane():
    nodes = {n.id: n for n in extract_flow_nodes(_xml())}
    task = nodes["Task_yes"]
    assert task.label == "Ship order"
    assert task.bpmn_type == "userTask"  # actor "Reviewer" is a role -> userTask (app/bpmn/mapping.py)
    assert task.lane_name == "Reviewer"
    # start event has no actor -- falls in the catch-all (no lane match)
    assert nodes["Event_start"].lane_name is None


def test_extract_flow_nodes_captures_predecessors_and_successors_with_conditions():
    nodes = {n.id: n for n in extract_flow_nodes(_xml())}
    gateway = nodes["Gateway_gw"]
    assert [p.node_id for p in gateway.predecessors] == ["Event_start"]
    assert [s.node_id for s in gateway.successors] == ["Task_yes"]
    assert gateway.successors[0].condition == "Approved"
    assert gateway.successors[0].label == "Ship order"

    end = nodes["Event_end"]
    assert end.successors == []
    assert [p.node_id for p in end.predecessors] == ["Task_yes"]


def test_extract_flow_nodes_returns_empty_list_when_no_process_element():
    assert extract_flow_nodes("<root/>") == []
