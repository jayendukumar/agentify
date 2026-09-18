"""US3.5: auto-layout. Rank-based BFS from start event(s), left-to-right,
per the bpmn-authoring skill -- not a full graph-layout library, just
"no overlapping shapes, edges routed sensibly".
"""

from __future__ import annotations

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


def compute_layout(model: BpmnModel) -> dict[str, NodeLayout]:
    adjacency: dict[str, list[str]] = {n.id: [] for n in model.nodes}
    for flow in model.flows:
        adjacency.setdefault(flow.source_ref, []).append(flow.target_ref)

    start_ids = [n.id for n in model.nodes if n.bpmn_type == "startEvent"]
    rank: dict[str, int] = {}
    queue: list[str] = list(start_ids) or [n.id for n in model.nodes[:1]]
    for node_id in queue:
        rank[node_id] = 0

    while queue:
        current = queue.pop(0)
        for neighbor in adjacency.get(current, []):
            candidate_rank = rank[current] + 1
            if neighbor not in rank or candidate_rank > rank[neighbor]:
                rank[neighbor] = candidate_rank
                queue.append(neighbor)

    # Anything BFS never reached (disconnected from a start event -- can
    # happen with incomplete LLM extraction output) still needs a rank so
    # every node gets laid out rather than silently dropped.
    max_rank = max(rank.values(), default=0)
    for node in model.nodes:
        if node.id not in rank:
            max_rank += 1
            rank[node.id] = max_rank

    lane_order = {lane.id: index for index, lane in enumerate(model.lanes)}
    nodes_by_rank: dict[int, list] = {}
    for node in model.nodes:
        nodes_by_rank.setdefault(rank[node.id], []).append(node)

    layout: dict[str, NodeLayout] = {}
    for rank_value, nodes_in_rank in nodes_by_rank.items():
        nodes_in_rank.sort(key=lambda n: (lane_order.get(n.lane_id, len(lane_order)), n.id))
        for row, node in enumerate(nodes_in_rank):
            width, height = SHAPE_SIZE.get(node.bpmn_type, (100, 80))
            x = _X_OFFSET + rank_value * _RANK_SPACING
            y = _Y_OFFSET + row * _ROW_SPACING
            layout[node.id] = NodeLayout(x=x, y=y, width=width, height=height)

    return layout
