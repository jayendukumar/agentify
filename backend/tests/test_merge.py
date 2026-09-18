from app.ingestion.merge import merge_process_schemas
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _schema(**kwargs) -> ProcessSchema:
    defaults = dict(process_name="P", actors=[], elements=[], flows=[])
    defaults.update(kwargs)
    return ProcessSchema(**defaults)


def _element(**kwargs) -> ProcessElement:
    defaults = dict(
        id="el-1",
        type="task",
        label="Submit the request",
        actor_id=None,
        inputs=[],
        outputs=[],
        systems_touched=[],
        source_refs=[SourceRef(document_id="d1", location="page 1", excerpt="x")],
        confidence="high",
    )
    defaults.update(kwargs)
    return ProcessElement(**defaults)


def test_distinct_labels_are_both_kept():
    existing = _schema(elements=[_element(id="el-1", label="Submit the request")])
    new = _schema(elements=[_element(id="el-2", label="Manager approves the invoice")])

    merged = merge_process_schemas(existing, new)

    assert {e.label for e in merged.elements} == {"Submit the request", "Manager approves the invoice"}
    assert len(merged.elements) == 2


def test_near_identical_labels_with_same_actor_are_merged():
    actor = Actor(id="a1", name="Employee", type="role")
    existing = _schema(
        actors=[actor],
        elements=[_element(id="el-1", label="Submit the expense request", actor_id="a1", confidence="medium")],
    )
    new_actor = Actor(id="a2", name="Employee", type="role")  # same person, different id from another doc
    new = _schema(
        actors=[new_actor],
        elements=[
            _element(
                id="el-2",
                label="Submit the expense request",  # exact duplicate description
                actor_id="a2",
                confidence="high",
                source_refs=[SourceRef(document_id="d2", location="paragraph 3", excerpt="y")],
            )
        ],
    )

    merged = merge_process_schemas(existing, new)

    assert len(merged.elements) == 1
    assert len(merged.actors) == 1  # deduped by (name, type)
    merged_element = merged.elements[0]
    assert merged_element.confidence == "high"  # kept the higher of the two
    assert len(merged_element.source_refs) == 2  # both documents' evidence preserved
    assert {r.document_id for r in merged_element.source_refs} == {"d1", "d2"}


def test_similar_labels_with_disagreeing_actors_are_kept_separate_and_flagged_low():
    actor_a = Actor(id="a1", name="Manager", type="role")
    actor_b = Actor(id="a2", name="Finance", type="role")
    existing = _schema(
        actors=[actor_a],
        elements=[_element(id="el-1", label="Approve the expense report", actor_id="a1", confidence="high")],
    )
    new = _schema(
        actors=[actor_b],
        elements=[_element(id="el-2", label="Approve the expense report", actor_id="a2", confidence="high")],
    )

    merged = merge_process_schemas(existing, new)

    assert len(merged.elements) == 2  # both kept, not collapsed into one
    assert all(e.confidence == "low" for e in merged.elements)  # both flagged for review


def test_flows_are_remapped_when_their_endpoints_get_merged():
    actor = Actor(id="a1", name="Employee", type="role")
    existing = _schema(
        actors=[actor],
        elements=[
            _element(id="el-1", label="Submit the request", actor_id="a1"),
            _element(id="el-2", label="Wait for approval", actor_id="a1"),
        ],
        flows=[ProcessFlow(id="f-1", **{"from": "el-1", "to": "el-2"})],
    )
    new = _schema(
        actors=[Actor(id="a2", name="Employee", type="role")],
        elements=[
            _element(id="el-9", label="Submit the request", actor_id="a2"),  # duplicate of el-1
            _element(id="el-10", label="Manager reviews it", actor_id="a2"),  # genuinely new
        ],
        flows=[ProcessFlow(id="f-2", **{"from": "el-9", "to": "el-10"})],
    )

    merged = merge_process_schemas(existing, new)

    # el-9 was merged into el-1, so the new flow's "from" must be remapped
    new_flow = next(f for f in merged.flows if f.id == "f-2")
    assert new_flow.from_ == "el-1"
    assert new_flow.to == "el-10"


def test_duplicate_flow_after_remap_is_not_added_twice():
    actor = Actor(id="a1", name="Employee", type="role")
    existing = _schema(
        actors=[actor],
        elements=[
            _element(id="el-1", label="Submit the request", actor_id="a1"),
            _element(id="el-2", label="Wait for approval", actor_id="a1"),
        ],
        flows=[ProcessFlow(id="f-1", **{"from": "el-1", "to": "el-2"})],
    )
    new = _schema(
        actors=[Actor(id="a2", name="Employee", type="role")],
        elements=[
            _element(id="el-9", label="Submit the request", actor_id="a2"),
            _element(id="el-10", label="Wait for approval", actor_id="a2"),
        ],
        flows=[ProcessFlow(id="f-2", **{"from": "el-9", "to": "el-10"})],
    )

    merged = merge_process_schemas(existing, new)

    assert len(merged.elements) == 2  # both pairs merged
    assert len(merged.flows) == 1  # the remapped duplicate flow was not re-added


def test_first_document_merge_is_a_straight_set():
    existing = _schema(elements=[])
    new = _schema(elements=[_element(id="el-1", label="Submit the request")])

    merged = merge_process_schemas(existing, new)

    assert len(merged.elements) == 1
