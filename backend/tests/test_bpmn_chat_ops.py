import pytest

from app.bpmn.builder import build_bpmn_xml
from app.bpmn.chat_ops import (
    DiagramDiffError,
    apply_diagram_diff,
    bpmn_id_for_element,
    bpmn_id_for_flow,
    humanize_validation_issues,
    strip_bpmn_id,
)
from app.bpmn.layout import compute_layout, extract_node_positions
from app.bpmn.mapping import map_schema_to_bpmn
from app.bpmn.validation import validate_bpmn
from app.schemas.chat import DiagramDiff, DiagramDiffOperation
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _element(**kwargs) -> ProcessElement:
    defaults = dict(id="e1", type="task", label="Do something", actor_id=None, source_refs=[_ref()], confidence="high")
    defaults.update(kwargs)
    return ProcessElement(**defaults)


def _flow(from_id: str, to_id: str, condition: str | None = None, id_: str | None = None) -> ProcessFlow:
    return ProcessFlow(id=id_ or f"f-{from_id}-{to_id}", **{"from": from_id}, to=to_id, condition=condition)


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


# -- id helpers ---------------------------------------------------------------


def test_bpmn_id_for_element_uses_type_specific_prefix():
    assert bpmn_id_for_element("e1", "task") == "Task_e1"
    assert bpmn_id_for_element("e1", "decision") == "Gateway_e1"
    assert bpmn_id_for_element("e1", "start_event") == "Event_e1"


def test_strip_bpmn_id_round_trips():
    assert strip_bpmn_id("Task_e1") == ("element", "e1")
    assert strip_bpmn_id("Gateway_d1") == ("element", "d1")
    assert strip_bpmn_id(bpmn_id_for_flow("f1")) == ("flow", "f1")
    assert strip_bpmn_id("Lane_a1") == ("lane", "a1")
    assert strip_bpmn_id("Unknown_x") is None


# -- apply_diagram_diff ---------------------------------------------------------


def test_apply_add_element_and_add_flow_mints_fresh_ids():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="add_node",
        summary="add a review step",
        operations=[
            DiagramDiffOperation(
                op="add_element",
                element={"type": "task", "label": "Review", "actor_id": "a1", "inputs": [], "outputs": [], "systems_touched": []},
            ),
        ],
    )
    updated = apply_diagram_diff(schema, diff)
    assert len(updated.elements) == 4
    new_element = updated.elements[-1]
    assert new_element.label == "Review"
    assert new_element.id not in {e.id for e in schema.elements}  # freshly minted, not LLM-supplied


def test_apply_add_node_wires_flow_to_new_element_via_temp_id():
    # Reproduces a real LLM response shape (Qwen3.7 Flash, manual testing):
    # an add_node diff needs an add_flow that connects TO the node being
    # added in the same diff, before the backend has minted its real id.
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="add_node",
        summary="insert a payment verification step",
        operations=[
            DiagramDiffOperation(op="remove_flow", flow_id="f-e1-e2"),
            DiagramDiffOperation(
                op="add_element",
                element={
                    "id": "new-1",
                    "type": "task",
                    "label": "Verify payment",
                    "actor_id": "a1",
                    "inputs": [],
                    "outputs": [],
                    "systems_touched": [],
                },
            ),
            DiagramDiffOperation(op="add_flow", flow={"from": "e1", "to": "new-1", "condition": None}),
            DiagramDiffOperation(op="add_flow", flow={"from": "new-1", "to": "e2", "condition": None}),
        ],
    )

    updated = apply_diagram_diff(schema, diff)
    new_element = next(e for e in updated.elements if e.label == "Verify payment")
    assert new_element.id != "new-1"  # temp id was replaced with a real minted id

    incoming = [f for f in updated.flows if f.to == new_element.id]
    outgoing = [f for f in updated.flows if f.from_ == new_element.id]
    assert len(incoming) == 1 and incoming[0].from_ == "e1"
    assert len(outgoing) == 1 and outgoing[0].to == "e2"


def test_apply_add_flow_missing_endpoint_raises():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="add_flow",
        summary="bad diff",
        operations=[DiagramDiffOperation(op="add_flow", flow={"from": "e1", "to": None, "condition": None})],
    )
    with pytest.raises(DiagramDiffError):
        apply_diagram_diff(schema, diff)


def test_apply_remove_element_and_reconnect():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="delete_node",
        summary="remove submit step",
        operations=[
            DiagramDiffOperation(op="remove_element", element_id="e2"),
            DiagramDiffOperation(op="remove_flow", flow_id="f-e1-e2"),
            DiagramDiffOperation(op="remove_flow", flow_id="f-e2-e3"),
            DiagramDiffOperation(op="add_flow", flow={"from": "e1", "to": "e3", "condition": None}),
        ],
    )
    updated = apply_diagram_diff(schema, diff)
    assert {e.id for e in updated.elements} == {"e1", "e3"}
    assert len(updated.flows) == 1
    assert updated.flows[0].from_ == "e1"
    assert updated.flows[0].to == "e3"


def test_apply_update_element_merges_fields():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="rename_node",
        summary="rename",
        operations=[DiagramDiffOperation(op="update_element", element_id="e2", fields={"label": "Renamed"})],
    )
    updated = apply_diagram_diff(schema, diff)
    renamed = next(e for e in updated.elements if e.id == "e2")
    assert renamed.label == "Renamed"
    assert renamed.actor_id == "a1"  # untouched fields survive the merge


def test_apply_update_flow_merges_fields():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="reroute_flow",
        summary="reroute",
        operations=[DiagramDiffOperation(op="update_flow", flow_id="f-e1-e2", fields={"to": "e3"})],
    )
    updated = apply_diagram_diff(schema, diff)
    rerouted = next(f for f in updated.flows if f.id == "f-e1-e2")
    assert rerouted.to == "e3"
    assert rerouted.from_ == "e1"


def test_apply_unknown_element_id_raises():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="rename_node",
        summary="rename",
        operations=[DiagramDiffOperation(op="update_element", element_id="does-not-exist", fields={"label": "x"})],
    )
    with pytest.raises(DiagramDiffError):
        apply_diagram_diff(schema, diff)


def test_apply_diagram_diff_does_not_mutate_input_schema():
    schema = _linear_schema()
    diff = DiagramDiff(
        intent="rename_node",
        summary="rename",
        operations=[DiagramDiffOperation(op="update_element", element_id="e2", fields={"label": "Renamed"})],
    )
    apply_diagram_diff(schema, diff)
    assert schema.elements[1].label == "Submit request"  # original untouched


# -- layout preservation --------------------------------------------------------


def test_extract_node_positions_reads_di_bounds():
    xml_str, _ = build_bpmn_xml("proc-1", _linear_schema())
    positions = extract_node_positions(xml_str)
    # extract_node_positions is a generic BPMNShape/Bounds reader -- it also
    # picks up the lane's own shape (harmless: compute_layout's
    # preferred_positions lookup only ever matches against model.nodes ids,
    # so a stray "Lane_a1" entry is just ignored there).
    assert {"Event_e1", "Task_e2", "Event_e3"} <= set(positions)
    assert positions["Event_e1"].x >= 0


def test_extract_node_positions_returns_empty_on_garbage():
    assert extract_node_positions("<not xml") == {}


def test_compute_layout_preserves_given_positions_for_unchanged_nodes():
    schema = _linear_schema()
    model = map_schema_to_bpmn("proc-1", schema)
    original_layout = compute_layout(model)

    # simulate: user dragged Task_e2 to a custom position
    moved = original_layout["Task_e2"]
    moved.x, moved.y = 999, 999
    preserved = {"Task_e2": moved}

    relaid_out = compute_layout(model, preserved)
    assert relaid_out["Task_e2"].x == 999
    assert relaid_out["Task_e2"].y == 999
    # nodes without a preferred position still get auto-placed
    assert "Event_e1" in relaid_out
    assert "Event_e3" in relaid_out


def test_compute_layout_auto_places_new_node_not_in_preferred_positions():
    schema = _linear_schema()
    schema.elements.append(_element(id="e4", type="task", label="New step"))
    schema.flows.append(_flow("e2", "e4"))
    model = map_schema_to_bpmn("proc-1", schema)

    preferred = compute_layout(map_schema_to_bpmn("proc-1", _linear_schema()))  # positions for e1/e2/e3 only
    layout = compute_layout(model, preferred)

    assert layout["Event_e1"] == preferred["Event_e1"]
    assert "Task_e4" in layout  # new node still gets placed


# -- humanize_validation_issues -------------------------------------------------


def test_humanize_validation_issues_replaces_element_id_with_label():
    schema = _linear_schema()
    # drop e3's incoming flow to reproduce a real orphan-node issue
    broken_schema = schema.model_copy(deep=True)
    broken_schema.flows = [f for f in broken_schema.flows if f.id != "f-e2-e3"]
    broken_xml, _ = build_bpmn_xml("proc-1", broken_schema)
    issues = validate_bpmn(broken_xml)
    assert any("Event_e3" in issue for issue in issues)  # sanity: raw id present before humanizing

    readable = humanize_validation_issues(issues, broken_schema)
    assert not any("Event_e3" in issue or "Task_e2" in issue for issue in readable)
    assert any("'End'" in issue for issue in readable)  # e3's label is "End"


def test_humanize_validation_issues_describes_flow_by_endpoints():
    schema = _linear_schema()
    dangling_issue = "sequenceFlow 'Flow_f-e1-e2' sourceRef 'Ghost_x' does not exist"
    readable = humanize_validation_issues([dangling_issue], schema)
    assert "the connection from 'Start' to 'Submit request'" in readable[0]


def test_humanize_validation_issues_leaves_unresolvable_ids_quoted_as_is():
    readable = humanize_validation_issues(["'Task_does-not-exist' has no incoming flow"], _linear_schema())
    assert readable == ["'Task_does-not-exist' has no incoming flow"]
