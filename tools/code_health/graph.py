"""Coupling and dependency cycles over an internal import graph.

Nodes are modules (or packages, after `collapse`). For each node:
  Ce (efferent)  = internal nodes it imports
  Ca (afferent)  = internal nodes importing it
  I  (instability) = Ce / (Ca + Ce): 0 = everything depends on it and it on
                     nothing (should be abstract and stable), 1 = depends on
                     much, nothing depends on it.
Cycles are the strongly connected components of size > 1 (Tarjan).
Edges carry a `deferred` flag (a function-level import), because a cycle
that only exists through lazy imports is a smell, not an import-time crash.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Edge:
    source: str
    target: str
    deferred: bool


@dataclass
class NodeCoupling:
    node: str
    afferent: int
    efferent: int

    @property
    def instability(self) -> float:
        total = self.afferent + self.efferent
        return round(self.efferent / total, 2) if total else 0.0


def collapse(edges: list[Edge], depth: int, prefix_parts: int = 0) -> list[Edge]:
    """Collapse module-level edges to package level: keep the first
    `prefix_parts + depth` dotted/slashed parts of each node name."""
    def key(name: str) -> str:
        sep = "/" if "/" in name else "."
        parts = name.split(sep)
        return sep.join(parts[: prefix_parts + depth])

    merged: dict[tuple[str, str], bool] = {}
    for e in edges:
        s, t = key(e.source), key(e.target)
        if s == t:
            continue
        merged[(s, t)] = merged.get((s, t), True) and e.deferred
    return [Edge(s, t, d) for (s, t), d in merged.items()]


def coupling(nodes: list[str], edges: list[Edge]) -> list[NodeCoupling]:
    out_edges: dict[str, set[str]] = defaultdict(set)
    in_edges: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        out_edges[e.source].add(e.target)
        in_edges[e.target].add(e.source)
    all_nodes = sorted(set(nodes) | set(out_edges) | set(in_edges))
    return [NodeCoupling(n, len(in_edges[n]), len(out_edges[n])) for n in all_nodes]


def cycles(edges: list[Edge], include_deferred: bool = True) -> list[list[str]]:
    graph: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if include_deferred or not e.deferred:
            graph[e.source].append(e.target)
            graph.setdefault(e.target, [])

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0

    # Iterative Tarjan, so deep graphs can't hit the recursion limit.
    for start in list(graph):
        if start in index:
            continue
        work = [(start, iter(graph[start]))]
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(graph[nxt])))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    result.append(sorted(component))
    result.sort(key=len, reverse=True)
    return result
