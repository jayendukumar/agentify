from app.bpmn.mapping import map_schema_to_bpmn
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _element(**kwargs) -> ProcessElement:
    defaults = dict(id="e1", type="task", label="Do something", actor_id=None, source_refs=[_ref()], confidence="high")
    defaults.update(kwargs)
    return ProcessElement(**defaults)


def _flow(from_id: str, to_id: str, condition: str | None = None) -> ProcessFlow:
    return ProcessFlow(id=f"f-{from_id}-{to_id}", **{"from": from_id}, to=to_id, condition=condition)


def test_event_types_map_correctly():
    schema = ProcessSchema(
        process_name="P",
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", type="end_event", label="End"),
            _element(id="e3", type="intermediate_event", label="Wait"),
        ],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    types = {n.source_element_id: n.bpmn_type for n in model.nodes}
    assert types == {"e1": "startEvent", "e2": "endEvent", "e3": "intermediateCatchEvent"}


def test_task_type_depends_on_actor_type():
    schema = ProcessSchema(
        process_name="P",
        actors=[Actor(id="a1", name="Person", type="role"), Actor(id="a2", name="CRM", type="system")],
        elements=[
            _element(id="e1", label="Human step", actor_id="a1"),
            _element(id="e2", label="System step", actor_id="a2"),
            _element(id="e3", label="Unattributed step", actor_id=None),
        ],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    types = {n.source_element_id: n.bpmn_type for n in model.nodes}
    assert types == {"e1": "userTask", "e2": "serviceTask", "e3": "task"}


def test_decision_with_all_conditioned_branches_is_exclusive_gateway():
    schema = ProcessSchema(
        process_name="P",
        elements=[_element(id="d1", type="decision", label="Approved?"), _element(id="e2"), _element(id="e3")],
        flows=[_flow("d1", "e2", condition="yes"), _flow("d1", "e3", condition="no")],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    gateway = next(n for n in model.nodes if n.source_element_id == "d1")
    assert gateway.bpmn_type == "exclusiveGateway"


def test_decision_with_no_conditions_is_parallel_gateway():
    schema = ProcessSchema(
        process_name="P",
        elements=[_element(id="d1", type="decision", label="Fan out"), _element(id="e2"), _element(id="e3")],
        flows=[_flow("d1", "e2"), _flow("d1", "e3")],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    gateway = next(n for n in model.nodes if n.source_element_id == "d1")
    assert gateway.bpmn_type == "parallelGateway"


def test_decision_with_mixed_conditions_is_inclusive_gateway():
    schema = ProcessSchema(
        process_name="P",
        elements=[_element(id="d1", type="decision", label="Mixed"), _element(id="e2"), _element(id="e3")],
        flows=[_flow("d1", "e2", condition="yes"), _flow("d1", "e3")],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    gateway = next(n for n in model.nodes if n.source_element_id == "d1")
    assert gateway.bpmn_type == "inclusiveGateway"


def test_actors_become_lanes_and_elements_are_assigned():
    schema = ProcessSchema(
        process_name="P",
        actors=[Actor(id="a1", name="Employee", type="role")],
        elements=[_element(id="e1", actor_id="a1"), _element(id="e2", actor_id=None)],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    assert len(model.lanes) == 1
    assert model.lanes[0].id == "Lane_a1"
    assert model.lanes[0].name == "Employee"

    by_element = {n.source_element_id: n for n in model.nodes}
    assert by_element["e1"].lane_id == "Lane_a1"
    assert by_element["e2"].lane_id is None


def test_low_confidence_elements_are_flagged():
    schema = ProcessSchema(
        process_name="P",
        elements=[
            _element(id="e1", confidence="high"),
            _element(id="e2", confidence="medium"),
            _element(id="e3", confidence="low"),
        ],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    assert set(model.low_confidence_node_ids) == {"Task_e2", "Task_e3"}


def test_flow_referencing_unknown_element_is_dropped_not_crashed():
    schema = ProcessSchema(
        process_name="P",
        elements=[_element(id="e1")],
        flows=[_flow("e1", "does-not-exist")],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    assert model.flows == []
