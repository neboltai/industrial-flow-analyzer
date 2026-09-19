"""Weighted directed aisle graph and shortest-path search.

The graph is built from ``layout.nodes`` and ``layout.edges``. A bidirectional
edge produces two directed arcs that share the same ``edge_id`` and distance.

Distances always come from ``distance_m`` on the edge. Euclidean node
coordinates are used for drawing only; they are never substituted for a missing
graph distance, because a straight line through a rack is not a walkable path.

Shortest paths use Dijkstra over :mod:`heapq`. Results are cached per
``(source, mode)`` as a full single-source tree, so repeated leg lookups inside
an order and across orders cost nothing after the first expansion.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Edge, Node


class GraphError(ValueError):
    """Raised when the graph cannot be constructed at all."""


@dataclass(frozen=True)
class Arc:
    edge_id: str
    from_node: str
    to_node: str
    distance_m: float
    allowed_modes: Tuple[str, ...]


@dataclass(frozen=True)
class PathResult:
    """A concrete shortest path between two nodes for one travel mode."""

    from_node: str
    to_node: str
    mode: str
    distance_m: float
    node_path: Tuple[str, ...]
    edge_path: Tuple[str, ...]


class Graph:
    """Directed, mode-aware, weighted graph with a single-source path cache."""

    def __init__(self, nodes: Sequence[Node], edges: Sequence[Edge]) -> None:
        self.nodes: Dict[str, Node] = {n.node_id: n for n in nodes}
        self.arcs: List[Arc] = []
        self._adjacency: Dict[str, List[Arc]] = {node_id: [] for node_id in self.nodes}
        self._tree_cache: Dict[Tuple[str, str], Tuple[Dict[str, float], Dict[str, Tuple[str, str]]]] = {}

        for edge in edges:
            if edge.from_node not in self.nodes or edge.to_node not in self.nodes:
                # Dangling references are reported by validation; the graph simply
                # skips the arc so that path search stays well-defined.
                continue
            if edge.distance_m <= 0:
                continue
            self._add_arc(
                Arc(edge.edge_id, edge.from_node, edge.to_node, edge.distance_m, edge.allowed_modes)
            )
            if edge.bidirectional:
                self._add_arc(
                    Arc(
                        edge.edge_id,
                        edge.to_node,
                        edge.from_node,
                        edge.distance_m,
                        edge.allowed_modes,
                    )
                )

    # ---------------------------------------------------------------- build

    def _add_arc(self, arc: Arc) -> None:
        self.arcs.append(arc)
        self._adjacency.setdefault(arc.from_node, []).append(arc)

    def outgoing(self, node_id: str, mode: str) -> List[Arc]:
        return [a for a in self._adjacency.get(node_id, []) if mode in a.allowed_modes]

    def node_ids(self) -> List[str]:
        return list(self.nodes)

    def modes(self) -> List[str]:
        seen: List[str] = []
        for arc in self.arcs:
            for mode in arc.allowed_modes:
                if mode not in seen:
                    seen.append(mode)
        return sorted(seen)

    # ------------------------------------------------------------- Dijkstra

    def _single_source(
        self, source: str, mode: str
    ) -> Tuple[Dict[str, float], Dict[str, Tuple[str, str]]]:
        """Dijkstra from ``source``; returns ``(distances, predecessors)``.

        ``predecessors[node] = (previous_node, edge_id)``.
        """
        cache_key = (source, mode)
        cached = self._tree_cache.get(cache_key)
        if cached is not None:
            return cached

        distances: Dict[str, float] = {source: 0.0}
        predecessors: Dict[str, Tuple[str, str]] = {}
        visited: Dict[str, bool] = {}
        heap: List[Tuple[float, str]] = [(0.0, source)]

        while heap:
            current_distance, node_id = heapq.heappop(heap)
            if visited.get(node_id):
                continue
            visited[node_id] = True
            for arc in self.outgoing(node_id, mode):
                candidate = current_distance + arc.distance_m
                known = distances.get(arc.to_node)
                # Ties are broken deterministically by edge_id so that repeated
                # runs on the same dataset produce byte-identical routes.
                if known is None or candidate < known - 1e-12:
                    distances[arc.to_node] = candidate
                    predecessors[arc.to_node] = (node_id, arc.edge_id)
                    heapq.heappush(heap, (candidate, arc.to_node))
                elif abs(candidate - known) <= 1e-12:
                    existing = predecessors.get(arc.to_node)
                    if existing is not None and arc.edge_id < existing[1]:
                        predecessors[arc.to_node] = (node_id, arc.edge_id)

        self._tree_cache[cache_key] = (distances, predecessors)
        return distances, predecessors

    def shortest_path(self, from_node: str, to_node: str, mode: str) -> Optional[PathResult]:
        """Shortest path or ``None`` when no route exists for that mode."""
        if from_node not in self.nodes or to_node not in self.nodes:
            return None
        if from_node == to_node:
            return PathResult(from_node, to_node, mode, 0.0, (from_node,), ())

        distances, predecessors = self._single_source(from_node, mode)
        if to_node not in distances:
            return None

        node_path: List[str] = [to_node]
        edge_path: List[str] = []
        cursor = to_node
        guard = 0
        while cursor != from_node:
            guard += 1
            if guard > len(self.nodes) + 1:  # pragma: no cover - defensive
                return None
            previous, edge_id = predecessors[cursor]
            node_path.append(previous)
            edge_path.append(edge_id)
            cursor = previous
        node_path.reverse()
        edge_path.reverse()
        return PathResult(
            from_node=from_node,
            to_node=to_node,
            mode=mode,
            distance_m=distances[to_node],
            node_path=tuple(node_path),
            edge_path=tuple(edge_path),
        )

    def distance(self, from_node: str, to_node: str, mode: str) -> Optional[float]:
        result = self.shortest_path(from_node, to_node, mode)
        return None if result is None else result.distance_m

    def reachable_from(self, source: str, mode: str) -> Dict[str, float]:
        distances, _ = self._single_source(source, mode)
        return dict(distances)

    def cache_size(self) -> int:
        """Number of cached single-source trees (used by tests and reports)."""
        return len(self._tree_cache)

    # -------------------------------------------------------- connectivity

    def weakly_connected_components(self) -> List[List[str]]:
        """Components ignoring arc direction and mode.

        A disconnected layout is a data error: it makes whole zones unreachable.
        """
        undirected: Dict[str, List[str]] = {node_id: [] for node_id in self.nodes}
        for arc in self.arcs:
            undirected[arc.from_node].append(arc.to_node)
            undirected[arc.to_node].append(arc.from_node)

        seen: Dict[str, bool] = {}
        components: List[List[str]] = []
        for node_id in sorted(self.nodes):
            if seen.get(node_id):
                continue
            stack = [node_id]
            component: List[str] = []
            seen[node_id] = True
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbour in undirected.get(current, []):
                    if not seen.get(neighbour):
                        seen[neighbour] = True
                        stack.append(neighbour)
            components.append(sorted(component))
        return components

    def unreachable_nodes(self, source: str, mode: str) -> List[str]:
        distances, _ = self._single_source(source, mode)
        return sorted(node_id for node_id in self.nodes if node_id not in distances)


def build_graph(nodes: Iterable[Node], edges: Iterable[Edge]) -> Graph:
    return Graph(list(nodes), list(edges))
