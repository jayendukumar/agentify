"""Swim-lane banding (app/bpmn/layout.py): reproduces the real bug found
against a real multi-actor document (HR Onboarding) -- lanes never
rendered as visible bands at all (see builder.py/validation.py changes and
the decision log), and even the semantic node positions weren't
lane-consistent across ranks."""

from app.bpmn.builder import build_bpmn_xml
from app.bpmn.layout import NodeLayout, compute_edge_waypoints, compute_lane_bounds, compute_layout
from app.bpmn.mapping import map_schema_to_bpmn
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _element(**kwargs) -> ProcessElement:
    defaults = dict(id="e1", type="task", label="Do something", actor_id=None, source_refs=[_ref()], confidence="high")
    defaults.update(kwargs)
    return ProcessElement(**defaults)


def _flow(from_id: str, to_id: str, id_: str | None = None) -> ProcessFlow:
    return ProcessFlow(id=id_ or f"f-{from_id}-{to_id}", **{"from": from_id}, to=to_id)


def _multi_actor_schema() -> ProcessSchema:
    """Mirrors the real HR Onboarding shape that exposed the bug: three
    actors, and -- critically -- not every rank has a node from every
    actor, which is exactly what broke the old row-within-rank math."""
    return ProcessSchema(
        process_name="Onboarding",
        actors=[
            Actor(id="hr", name="HR", type="system"),
            Actor(id="mgr", name="Hiring Manager", type="role"),
            Actor(id="it", name="IT", type="system"),
        ],
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", label="HR sends contract", actor_id="hr"),
            _element(id="e3", label="Manager preps plan", actor_id="mgr"),
            _element(id="e4", label="IT preps equipment", actor_id="it"),
            _element(id="e5", label="HR sends welcome", actor_id="hr"),
            _element(id="e6", type="end_event", label="End"),
        ],
        # rank 0: e1 (no lane). rank 1: e2 (hr), e3 (mgr), e4 (it) --
        # every lane present. rank 2: e5 (hr) only -- mgr/it absent at
        # this rank, which is exactly the case the old algorithm mishandled.
        flows=[
            _flow("e1", "e2"),
            _flow("e1", "e3"),
            _flow("e1", "e4"),
            _flow("e2", "e5"),
            _flow("e5", "e6"),
            _flow("e3", "e6"),
            _flow("e4", "e6"),
        ],
    )


def test_nodes_in_the_same_lane_share_y_across_different_ranks():
    schema = _multi_actor_schema()
    model = map_schema_to_bpmn("proc-1", schema)
    layout = compute_layout(model)

    # e2 (rank 1) and e5 (rank 2) are both HR -- same lane, must land on
    # the same Y even though they're at different ranks/x positions.
    assert layout["Task_e2"].y == layout["Task_e5"].y
    assert layout["Task_e2"].x != layout["Task_e5"].x


def test_different_lanes_never_share_a_y_band():
    schema = _multi_actor_schema()
    model = map_schema_to_bpmn("proc-1", schema)
    layout = compute_layout(model)

    hr_y = layout["Task_e2"].y
    mgr_y = layout["Task_e3"].y
    it_y = layout["Task_e4"].y
    assert len({hr_y, mgr_y, it_y}) == 3  # three distinct bands, not colliding rows


def test_lane_band_height_grows_for_concurrent_same_actor_nodes():
    schema = ProcessSchema(
        process_name="P",
        actors=[Actor(id="hr", name="HR", type="system")],
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", label="Task A", actor_id="hr"),
            _element(id="e3", label="Task B", actor_id="hr"),  # same rank as e2, same actor
        ],
        flows=[_flow("e1", "e2"), _flow("e1", "e3")],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    layout = compute_layout(model)

    # two concurrent same-lane nodes must not overlap vertically
    assert layout["Task_e2"].y != layout["Task_e3"].y
    assert layout["Task_e2"].x == layout["Task_e3"].x  # same rank


def test_compute_lane_bounds_encloses_members_with_shared_x_extent():
    schema = _multi_actor_schema()
    model = map_schema_to_bpmn("proc-1", schema)
    layout = compute_layout(model)
    lane_bounds = compute_lane_bounds(model, layout)

    assert set(lane_bounds) == {"Lane_hr", "Lane_mgr", "Lane_it"}

    hr_box = lane_bounds["Lane_hr"]
    e2, e5 = layout["Task_e2"], layout["Task_e5"]
    assert hr_box.y <= e2.y and hr_box.y <= e5.y
    assert hr_box.y + hr_box.height >= e2.y + e2.height
    assert hr_box.y + hr_box.height >= e5.y + e5.height

    # every lane shares the same X extent -- true swimlanes, not
    # independent boxes sized to their own content only.
    xs = {(box.x, box.width) for box in lane_bounds.values()}
    assert len(xs) == 1


def test_compute_lane_bounds_gives_empty_lane_a_placeholder_band():
    schema = ProcessSchema(
        process_name="P",
        actors=[Actor(id="hr", name="HR", type="system"), Actor(id="ghost", name="Nobody", type="role")],
        elements=[_element(id="e1", label="Task", actor_id="hr")],
        flows=[],
    )
    model = map_schema_to_bpmn("proc-1", schema)
    layout = compute_layout(model)
    lane_bounds = compute_lane_bounds(model, layout)

    assert set(lane_bounds) == {"Lane_hr", "Lane_ghost"}
    assert lane_bounds["Lane_ghost"].height > 0
    # stacked below the populated lane, not overlapping it
    assert lane_bounds["Lane_ghost"].y >= lane_bounds["Lane_hr"].y + lane_bounds["Lane_hr"].height


def test_compute_layout_terminates_on_a_cyclic_flow_graph():
    # Real defect found live: a genuine rework loop in a real extracted
    # document ("Confirm start clearance" <-> "Deferral of start if checks
    # incomplete") hung the single-threaded API process indefinitely -- the
    # rank BFS's longest-path relaxation re-queued the looping nodes
    # forever. A rework loop isn't malformed extraction output; real
    # processes have them. Runs off-thread with a hard wall-clock bound so
    # a regression here fails this test instead of hanging the whole suite.
    schema = ProcessSchema(
        process_name="P",
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", label="Gate"),
            _element(id="e3", label="Escalate and defer"),
            _element(id="e4", type="end_event", label="End"),
        ],
        flows=[
            _flow("e1", "e2"),
            _flow("e2", "e3"),
            _flow("e3", "e2"),  # rework loop back to the gate
            _flow("e2", "e4"),
        ],
    )
    model = map_schema_to_bpmn("proc-1", schema)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        layout = pool.submit(compute_layout, model).result(timeout=5)

    assert set(layout) == {"Event_e1", "Task_e2", "Task_e3", "Event_e4"}


def test_build_bpmn_xml_terminates_on_a_cyclic_flow_graph():
    schema = ProcessSchema(
        process_name="P",
        actors=[Actor(id="a1", name="Reviewer", type="role")],
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", label="Gate", actor_id="a1"),
            _element(id="e3", label="Escalate and defer", actor_id="a1"),
            _element(id="e4", type="end_event", label="End"),
        ],
        flows=[_flow("e1", "e2"), _flow("e2", "e3"), _flow("e3", "e2"), _flow("e2", "e4")],
    )

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        xml_str, _ = pool.submit(build_bpmn_xml, "proc-1", schema).result(timeout=5)

    assert "Task_e2" in xml_str and "Task_e3" in xml_str


def test_compute_lane_bounds_empty_model_returns_nothing():
    assert compute_lane_bounds(map_schema_to_bpmn("proc-1", ProcessSchema(process_name="P")), {}) == {}


def test_compute_layout_preserved_positions_still_used_verbatim_for_lane_bounds():
    # Lane bounds must reflect wherever a node *actually* is, including a
    # preserved (e.g. manually dragged, Epic 5) position -- not recomputed
    # from scratch and ignoring it.
    schema = _multi_actor_schema()
    model = map_schema_to_bpmn("proc-1", schema)
    fresh = compute_layout(model)
    dragged = NodeLayout(x=fresh["Task_e2"].x, y=9999, width=100, height=80)
    layout = compute_layout(model, {"Task_e2": dragged})

    lane_bounds = compute_lane_bounds(model, layout)
    assert lane_bounds["Lane_hr"].y + lane_bounds["Lane_hr"].height >= 9999 + 80


# -- compute_edge_waypoints ------------------------------------------------------


def test_edge_waypoints_same_lane_is_a_single_straight_segment():
    source = NodeLayout(x=100, y=80, width=100, height=80)
    target = NodeLayout(x=280, y=80, width=100, height=80)
    waypoints = compute_edge_waypoints(source, target, diagram_bottom=200)
    assert waypoints == [(source.right, source.center_y), (target.left, target.center_y)]


def test_edge_waypoints_different_lane_is_orthogonal_not_diagonal():
    # Real complaint this fixes: a straight source-center to target-center
    # line cuts diagonally across every lane band and node in between once
    # real swimlanes exist. The fix must never draw a line whose points
    # aren't axis-aligned to each other.
    source = NodeLayout(x=100, y=80, width=100, height=80)
    target = NodeLayout(x=460, y=440, width=100, height=80)
    waypoints = compute_edge_waypoints(source, target, diagram_bottom=600)

    assert waypoints[0] == (source.right, source.center_y)
    assert waypoints[-1] == (target.left, target.center_y)
    for (x1, y1), (x2, y2) in zip(waypoints, waypoints[1:]):
        assert x1 == x2 or y1 == y2  # every segment is horizontal or vertical, never both

    # the vertical bend sits in the gap between rank columns, not on top
    # of either node
    bend_x = waypoints[1][0]
    assert bend_x > source.right
    assert bend_x < target.left


def test_edge_waypoints_backward_flow_routes_as_a_loop_below_the_diagram():
    # Real defect this fixes: a rework/retry loop (target at or behind
    # source's own rank -- see the decision log's cyclic-flow-graph entry)
    # used to get the exact same "straight line to target's center"
    # treatment, which runs backward through everything between them.
    source = NodeLayout(x=460, y=80, width=100, height=80)  # later rank
    target = NodeLayout(x=100, y=80, width=100, height=80)  # earlier rank -- a loop back
    diagram_bottom = 300
    waypoints = compute_edge_waypoints(source, target, diagram_bottom)

    assert waypoints[0] == (source.center_x, source.bottom)
    assert waypoints[-1] == (target.center_x, target.bottom)
    loop_y = waypoints[1][1]
    assert loop_y > diagram_bottom  # clears every node in the diagram, not just these two
    assert waypoints[1][1] == waypoints[2][1]  # the loop's horizontal run is a single flat segment


def test_edge_waypoints_same_rank_different_lane_also_loops_not_diagonal():
    # Same rank (dx == 0) isn't "forward" either -- must not fall through
    # to a diagonal straight line.
    source = NodeLayout(x=100, y=80, width=100, height=80)
    target = NodeLayout(x=100, y=440, width=100, height=80)
    waypoints = compute_edge_waypoints(source, target, diagram_bottom=600)
    for (x1, y1), (x2, y2) in zip(waypoints, waypoints[1:]):
        assert x1 == x2 or y1 == y2


def test_build_bpmn_xml_uses_orthogonal_routing_for_the_rework_loop():
    import xml.etree.ElementTree as ET

    schema = ProcessSchema(
        process_name="P",
        actors=[Actor(id="a1", name="Reviewer", type="role")],
        elements=[
            _element(id="e1", type="start_event", label="Start"),
            _element(id="e2", label="Gate", actor_id="a1"),
            _element(id="e3", label="Escalate and defer", actor_id="a1"),
            _element(id="e4", type="end_event", label="End"),
        ],
        flows=[
            _flow("e1", "e2", id_="f-forward-1"),
            _flow("e2", "e3", id_="f-forward-2"),
            _flow("e3", "e2", id_="f-loop-back"),
            _flow("e2", "e4", id_="f-forward-3"),
        ],
    )
    xml_str, _ = build_bpmn_xml("proc-1", schema)
    root = ET.fromstring(xml_str)

    edges_by_flow = {
        edge.get("bpmnElement"): edge.findall("{http://www.omg.org/spec/DD/20100524/DI}waypoint")
        for edge in root.iter("{http://www.omg.org/spec/BPMN/20100524/DI}BPMNEdge")
    }
    # the loop-back edge needs 4 waypoints (down, across, up) -- a straight
    # 2-point line would run backward through the gate/escalate nodes
    assert len(edges_by_flow["Flow_f-loop-back"]) == 4
    # a normal forward, same-lane edge stays a simple 2-point line
    assert len(edges_by_flow["Flow_f-forward-2"]) == 2
