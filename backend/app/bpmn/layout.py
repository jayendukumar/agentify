"""US3.5: auto-layout. Rank-based BFS from start event(s) gives each node an
X position (time/sequence, left-to-right); each actor's lane gets a fixed,
non-overlapping vertical band for Y, per the bpmn-authoring skill -- not a
full graph-layout library, just "no overlapping shapes, edges routed
sensibly, and actual swimlanes for a multi-actor process."

Epic 5: compute_layout also accepts preferred_positions (extracted from a
process's current draft via extract_node_positions) so a chat-applied diff
can keep unchanged nodes where the user left them on canvas and only
auto-place newly added ones -- see app/bpmn/chat_ops.py and the decision
log for why this isn't a full incremental-layout solution (a newly placed
node can still overlap a preserved one if the graph shape changed a lot;
the "Refresh Layout" UI action is the escape hatch, a full relayout via
the unchanged, positions-free call path).

compute_lane_bounds (added alongside a real defect fix -- see the decision
log's "swim lanes never rendered" entry) computes the DI bounds for each
*lane itself*, not just its member nodes. bpmn-js only draws a lane's
visual band when a <bpmndi:BPMNShape> exists for the lane id (confirmed by
reading bpmn-js's own BpmnTreeWalker/visitIfDi source, not assumed) -- a
laneSet with populated flowNodeRefs but no lane DI shapes renders as a flat
scatter of nodes with no visible swimlane separation at all, regardless of
how many actors the schema has.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .mapping import BpmnModel

# Standard bpmn-js-compatible shape sizes.
SHAPE_SIZE = {
    "task": (100, 80),
    "userTask": (100, 80),
    "serviceTask": (100, 80),
    "exclusiveGateway": (50, 50),
    "parallelGateway": (50, 50),
    "inclusiveGateway": (50, 50),
    "startEvent": (36, 36),
    "endEvent": (36, 36),
    "intermediateCatchEvent": (36, 36),
}

_RANK_SPACING = 180
_ROW_SPACING = 120
_X_OFFSET = 100
_Y_OFFSET = 80
_LANE_PADDING = 30  # vertical breathing room between a lane's edge and its nodes
_LANE_X_MARGIN = 30  # horizontal overhang of a lane band past its leftmost/rightmost node


@dataclass
class NodeLayout:
    x: int
    y: int
    width: int
    height: int

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def left(self) -> int:
        return self.x

    @property
    def top(self) -> int:
        return self.y

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2


def compute_layout(
    model: BpmnModel, preferred_positions: dict[str, NodeLayout] | None = None
) -> dict[str, NodeLayout]:
    preferred_positions = preferred_positions or {}
    remaining_nodes = [n for n in model.nodes if n.id not in preferred_positions]

    layout: dict[str, NodeLayout] = {
        node_id: box for node_id, box in preferred_positions.items() if node_id in {n.id for n in model.nodes}
    }
    if not remaining_nodes:
        return layout

    layout.update(_compute_layout_for(model, remaining_nodes))
    return layout


def _rank_nodes(model: BpmnModel) -> dict[str, int]:
    """Assigns each node an X-rank (time/sequence position) via Kahn's
    algorithm: a node is only finalized once every one of its predecessors
    already has a rank, so rank[node] = max(predecessor ranks) + 1 falls
    out correctly (a convergence point lands after ALL its branches, not
    just whichever one BFS happened to reach first) without ever needing
    to revisit or update a node's rank once assigned.

    That "never revisit" property is what makes this safe on a graph with
    a cycle, which a real extracted process can have -- a rework/retry
    loop (e.g. "if a pre-employment check is incomplete, defer and
    re-confirm start clearance") isn't malformed extraction output, just
    something a strict left-to-right rank can't represent for the nodes
    actually inside the loop. The previous approach (relaxation: re-queue
    a node whenever a *longer* path to it is found) is the textbook way to
    rank a DAG but never terminates on a cycle -- found live, a real
    document with exactly this loop hung the single-threaded API process
    indefinitely (see the decision log). Kahn's algorithm instead simply
    never finalizes a node whose predecessor-chain doesn't bottom out in
    already-ranked nodes -- which is exactly true of every node in (or
    only reachable via) a cycle, and is used here as the *signal* for
    "this component needs the cycle fallback" below, not a workaround
    bolted on after the fact.
    """
    adjacency: dict[str, list[str]] = {n.id: [] for n in model.nodes}
    in_degree: dict[str, int] = {n.id: 0 for n in model.nodes}
    for flow in model.flows:
        adjacency.setdefault(flow.source_ref, []).append(flow.target_ref)
        if flow.target_ref in in_degree:
            in_degree[flow.target_ref] += 1

    start_ids = [n.id for n in model.nodes if n.bpmn_type == "startEvent"]
    seeds = start_ids or [node_id for node_id, degree in in_degree.items() if degree == 0]
    if not seeds and model.nodes:
        seeds = [model.nodes[0].id]

    candidate_rank: dict[str, int] = {node_id: 0 for node_id in seeds}
    remaining_in_degree = dict(in_degree)
    for node_id in seeds:
        remaining_in_degree[node_id] = 0

    rank: dict[str, int] = {}
    queue = list(seeds)
    while queue:
        current = queue.pop(0)
        rank[current] = candidate_rank[current]
        for neighbor in adjacency.get(current, []):
            if neighbor in rank:
                continue
            candidate_rank[neighbor] = max(candidate_rank.get(neighbor, 0), rank[current] + 1)
            remaining_in_degree[neighbor] -= 1
            if remaining_in_degree[neighbor] <= 0:
                queue.append(neighbor)

    # Anything left unranked is part of a cycle, or only reachable through
    # one (its in-degree from within the cycle never resolves to 0) -- this
    # also covers a node disconnected from any start event entirely, the
    # case this fallback originally handled alone. Break each remaining
    # component with a plain single-visit BFS: a node's rank, once
    # assigned here, is never revisited, so this always terminates too.
    max_rank = max(rank.values(), default=-1)
    for node in model.nodes:
        if node.id in rank:
            continue
        max_rank += 1
        rank[node.id] = max_rank
        component_queue = [node.id]
        while component_queue:
            current = component_queue.pop(0)
            for neighbor in adjacency.get(current, []):
                if neighbor in rank:
                    continue
                max_rank += 1
                rank[neighbor] = max_rank
                component_queue.append(neighbor)

    return rank


def _compute_layout_for(model: BpmnModel, nodes: list) -> dict[str, NodeLayout]:
    """Ranks/places every node in `model` (so new nodes are positioned
    relative to preserved ones), but only returns layouts for the ids in
    `nodes` -- the caller already has positions for the rest."""
    wanted_ids = {n.id for n in nodes}
    rank = _rank_nodes(model)

    # Swimlane bands: every node's lane_id (None = no actor assigned --
    # grouped into its own catch-all band after the named lanes) gets a
    # fixed vertical band, in lane declaration order, sized for however
    # many of its nodes land on the same rank (usually 1, but the same
    # actor can have more than one step at a given point in the sequence).
    # Y no longer depends on which OTHER lanes have a node at a given rank
    # -- that was the bug: a rank with only lanes A and C present put C's
    # node at "row 1"'s y, colliding with a *different* rank's lane-B node
    # also at "row 1"'s y, because "row" was an index within the rank, not
    # a stable position per lane.
    lane_order = {lane.id: index for index, lane in enumerate(model.lanes)}
    band_order = sorted(
        {node.lane_id for node in model.nodes}, key=lambda lane_id: lane_order.get(lane_id, len(lane_order))
    )

    concurrency: dict[tuple[str | None, int], int] = {}
    for node in model.nodes:
        key = (node.lane_id, rank[node.id])
        concurrency[key] = concurrency.get(key, 0) + 1

    band_top: dict[str | None, int] = {}
    cursor = _Y_OFFSET
    for lane_id in band_order:
        band_top[lane_id] = cursor
        max_concurrent = max((count for key, count in concurrency.items() if key[0] == lane_id), default=1)
        cursor += max(max_concurrent, 1) * _ROW_SPACING + _LANE_PADDING * 2

    row_in_band: dict[tuple[str | None, int], int] = {}
    layout: dict[str, NodeLayout] = {}
    for node in model.nodes:
        if node.id not in wanted_ids:
            continue
        width, height = SHAPE_SIZE.get(node.bpmn_type, (100, 80))
        key = (node.lane_id, rank[node.id])
        row = row_in_band.get(key, 0)
        row_in_band[key] = row + 1
        x = _X_OFFSET + rank[node.id] * _RANK_SPACING
        y = band_top[node.lane_id] + _LANE_PADDING + row * _ROW_SPACING
        layout[node.id] = NodeLayout(x=x, y=y, width=width, height=height)

    return layout


def compute_lane_bounds(model: BpmnModel, node_layout: dict[str, NodeLayout]) -> dict[str, NodeLayout]:
    """DI bounds for each declared *lane itself* (not its member nodes) --
    the tight enclosing rectangle of its members' actual current positions
    (whether freshly auto-placed or preserved from a prior draft, Epic 5),
    padded, with every lane's X-extent normalized to the full diagram width
    so the bands read as one shared swimlane, not independent boxes.
    Expects `node_layout` to cover every node in `model` (builder.py's only
    caller passes the full merged layout); an empty lane (no elements
    currently assigned to that actor) still gets a thin placeholder band,
    stacked below the populated ones, rather than vanishing from the
    diagram entirely.
    """
    if not model.lanes or not node_layout:
        return {}

    all_left = min(box.left for box in node_layout.values())
    all_right = max(box.right for box in node_layout.values())
    width = (all_right - all_left) + _LANE_X_MARGIN * 2
    x = all_left - _LANE_X_MARGIN

    nodes_by_lane: dict[str, list[str]] = {}
    for node in model.nodes:
        if node.lane_id and node.id in node_layout:
            nodes_by_lane.setdefault(node.lane_id, []).append(node.id)

    bounds: dict[str, NodeLayout] = {}
    empty_lanes = []
    for lane in model.lanes:
        member_ids = nodes_by_lane.get(lane.id, [])
        if not member_ids:
            empty_lanes.append(lane)
            continue
        top = min(node_layout[nid].y for nid in member_ids) - _LANE_PADDING
        bottom = max(node_layout[nid].y + node_layout[nid].height for nid in member_ids) + _LANE_PADDING
        bounds[lane.id] = NodeLayout(x=x, y=top, width=width, height=bottom - top)

    cursor = max((box.y + box.height for box in bounds.values()), default=_Y_OFFSET)
    for lane in empty_lanes:
        height = _ROW_SPACING + _LANE_PADDING * 2
        bounds[lane.id] = NodeLayout(x=x, y=cursor, width=width, height=height)
        cursor += height

    return bounds


_LOOP_MARGIN = 40  # vertical clearance below the lowest node before a backward/loop edge routes back


def compute_edge_waypoints(
    source: NodeLayout, target: NodeLayout, diagram_bottom: int
) -> list[tuple[int, int]]:
    """Orthogonal (Manhattan-style) routing, not a straight source-center
    to target-center line -- found live that a straight diagonal line
    reads as visual noise once real swimlanes exist (US3.5's earlier
    "just connect the centers" approach cuts diagonally across lane bands
    and unrelated nodes the moment source/target aren't in the same lane
    at adjacent ranks, which is the common case in any multi-actor
    process). Three cases:

    - Same lane (same Y): a single straight horizontal segment -- already
      about as clean as routing gets, no need to bend it.
    - Forward (target starts at or after source ends) but a different
      lane: exit the source's right edge, run vertically at the midpoint
      between the two ranks (the gap between rank columns, where no node
      ever sits), enter the target's left edge. Doesn't guarantee zero
      crossings for a flow that skips multiple ranks, but replaces a
      diagonal cutting through everything between them with two axis
      -aligned bends, which is what every real BPMN tool draws.
    - Backward (target does not start after source ends -- a rework/retry
      loop, which real extracted processes can have, see the decision
      log's cyclic-flow-graph entry): a straight or forward-routed line
      would run backward through everything in between. Routed below the
      *entire* diagram instead (diagram_bottom, not just these two nodes'
      own bottoms) as a loop: down from the source, left/right to the
      target's column, up into the target -- the same convention real BPMN
      tools use for a loop-back edge.
    """
    if target.left >= source.right:
        if source.center_y == target.center_y:
            return [(source.right, source.center_y), (target.left, target.center_y)]
        mid_x = source.right + (target.left - source.right) // 2
        return [
            (source.right, source.center_y),
            (mid_x, source.center_y),
            (mid_x, target.center_y),
            (target.left, target.center_y),
        ]

    loop_y = diagram_bottom + _LOOP_MARGIN
    return [
        (source.center_x, source.bottom),
        (source.center_x, loop_y),
        (target.center_x, loop_y),
        (target.center_x, target.bottom),
    ]


def extract_node_positions(xml_str: str) -> dict[str, NodeLayout]:
    """Parses <bpmndi:BPMNShape>/<dc:Bounds> out of an existing draft's
    XML, keyed by BPMN node id -- used as compute_layout's
    preferred_positions so a chat-applied diff keeps unchanged nodes where
    the user left them. Returns {} on unparseable input rather than
    raising -- the caller (chat-apply) should treat "no positions to
    preserve" as "fall back to full auto-layout", not a hard failure."""
    ns = {
        "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
        "dc": "http://www.omg.org/spec/DD/20100524/DC",
    }
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return {}

    positions: dict[str, NodeLayout] = {}
    for shape in root.iter(f"{{{ns['bpmndi']}}}BPMNShape"):
        element_id = shape.get("bpmnElement")
        bounds = shape.find(f"{{{ns['dc']}}}Bounds")
        if not element_id or bounds is None:
            continue
        try:
            positions[element_id] = NodeLayout(
                x=int(float(bounds.get("x", "0"))),
                y=int(float(bounds.get("y", "0"))),
                width=int(float(bounds.get("width", "0"))),
                height=int(float(bounds.get("height", "0"))),
            )
        except (TypeError, ValueError):
            continue
    return positions
