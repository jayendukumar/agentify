import xml.etree.ElementTree as ET

from app.bpmn.builder import build_bpmn_xml
from app.bpmn.validation import validate_bpmn
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _element(**kwargs) -> ProcessElement:
    defaults = dict(id="e1", type="task", label="Do something", actor_id=None, source_refs=[_ref()], confidence="high")
    defaults.update(kwargs)
    return ProcessElement(**defaults)


def _flow(from_id: str, to_id: str, condition: str | None = None) -> ProcessFlow:
    return ProcessFlow(id=f"f-{from_id}-{to_id}", **{"from": from_id}, to=to_id, condition=condition)


def _linear_schema() -> ProcessSchema:
    return ProcessSchema(
        process_name="Linear Process",
        actors=[Actor(id="a1", name="Employee", type="role")],
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", type="task", label="Submit request", actor_id="a1"),
            _element(id="e3", type="end_event", label="End"),
        ],
        flows=[_flow("e1", "e2"), _flow("e2", "e3")],
    )


def test_build_produces_well_formed_xml_with_correct_namespaces():
    xml_str, _ = build_bpmn_xml("proc-1", _linear_schema())
    root = ET.fromstring(xml_str)
    assert root.tag == "{http://www.omg.org/spec/BPMN/20100524/MODEL}definitions"
    assert root.get("id") == "Definitions_proc-1"


def test_build_includes_diagram_interchange():
    xml_str, _ = build_bpmn_xml("proc-1", _linear_schema())
    root = ET.fromstring(xml_str)
    diagram = root.find("{http://www.omg.org/spec/BPMN/20100524/DI}BPMNDiagram")
    assert diagram is not None
    shapes = diagram.findall(".//{http://www.omg.org/spec/BPMN/20100524/DI}BPMNShape")
    assert len(shapes) == 3  # start, task, end


def test_build_process_is_not_executable():
    xml_str, _ = build_bpmn_xml("proc-1", _linear_schema())
    root = ET.fromstring(xml_str)
    process = root.find("{http://www.omg.org/spec/BPMN/20100524/MODEL}process")
    assert process.get("isExecutable") == "false"


def test_generated_xml_passes_validation():
    xml_str, _ = build_bpmn_xml("proc-1", _linear_schema())
    assert validate_bpmn(xml_str) == []


def test_low_confidence_ids_are_source_schema_ids_not_bpmn_ids():
    schema = ProcessSchema(
        process_name="P",
        elements=[_element(id="step-a", confidence="low")],
    )
    _, low_confidence = build_bpmn_xml("proc-1", schema)
    assert low_confidence == ["step-a"]  # not "Task_step-a"


def test_diamond_shaped_flow_still_validates():
    # decision with two branches reconverging on a shared end event --
    # the "implicit join at a normal node" case app/bpmn/mapping.py's
    # docstring says is accepted rather than requiring a synthesized
    # join gateway.
    schema = ProcessSchema(
        process_name="P",
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="d1", type="decision", label="Which way?"),
            _element(id="e2", label="Path A"),
            _element(id="e3", label="Path B"),
            _element(id="e4", type="end_event", label="End"),
        ],
        flows=[
            _flow("e1", "d1"),
            _flow("d1", "e2", condition="a"),
            _flow("d1", "e3", condition="b"),
            _flow("e2", "e4"),
            _flow("e3", "e4"),
        ],
    )
    xml_str, _ = build_bpmn_xml("proc-1", schema)
    assert validate_bpmn(xml_str) == []


def test_validation_catches_dangling_sequence_flow_ref():
    bad_xml = """<?xml version="1.0"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions_x">
  <bpmn:process id="Process_x" isExecutable="false">
    <bpmn:startEvent id="Event_1" />
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Event_1" targetRef="Ghost_element" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_x">
    <bpmndi:BPMNPlane id="Plane_x" bpmnElement="Process_x">
      <bpmndi:BPMNShape id="Shape_1" bpmnElement="Event_1"><dc:Bounds x="0" y="0" width="36" height="36" /></bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Edge_1" bpmnElement="Flow_1"><di:waypoint x="0" y="0" /><di:waypoint x="10" y="10" /></bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""
    issues = validate_bpmn(bad_xml)
    assert any("Ghost_element" in issue for issue in issues)


def test_validation_catches_orphan_node_with_no_incoming_flow():
    xml_str, _ = build_bpmn_xml(
        "proc-1",
        ProcessSchema(process_name="P", elements=[_element(id="e1", type="task", label="Floating task")]),
    )
    # a lone task with no flows: not a start event, so "no incoming" should fire
    issues = validate_bpmn(xml_str)
    assert any("no incoming flow" in issue for issue in issues)


def test_validation_catches_missing_di_shape():
    bad_xml = """<?xml version="1.0"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions_x">
  <bpmn:process id="Process_x" isExecutable="false">
    <bpmn:startEvent id="Event_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_x">
    <bpmndi:BPMNPlane id="Plane_x" bpmnElement="Process_x" />
  </bpmndi:BPMNDiagram>
</bpmn:definitions>"""
    issues = validate_bpmn(bad_xml)
    assert any("no DI shape" in issue for issue in issues)


def test_validation_reports_not_well_formed_xml():
    issues = validate_bpmn("<not-even-close-to-xml")
    assert len(issues) == 1
    assert "well-formed" in issues[0]
