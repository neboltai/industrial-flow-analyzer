"""Reconstruction of the travelled route of each historical order.

A route is:

``route_start_node -> access node of pick 1 -> ... -> access node of pick n ->
route_end_node``

Consecutive picks at the same access node produce a zero-length leg; they are
counted as pick lines but add no travel. An order that contains at least one
unreachable leg is never counted as a complete route: its distance is reported
as ``None`` and the order is listed under ``unreachable_order_count``.

The pick sequence is taken exclusively from ``pick_sequence``. If any pick of an
order lacks it, the order is not routed at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .graph import Graph
from .models import Dataset, Pick


@dataclass
class RouteLeg:
    from_node: str
    to_node: str
    distance_m: Optional[float]
    edge_path: Tuple[str, ...]
    reachable: bool
    to_pick_id: Optional[str] = None


@dataclass
class OrderRoute:
    order_id: str
    mode: str
    complete: bool
    reason: Optional[str]
    total_distance_m: Optional[float]
    legs: List[RouteLeg] = field(default_factory=list)
    stop_nodes: List[str] = field(default_factory=list)
    edge_traversals: Dict[str, int] = field(default_factory=dict)
    zone_transitions: int = 0
    number_of_picks: int = 0
    number_of_lines: int = 0
    total_units: float = 0.0
    unreachable_segments: List[Dict[str, str]] = field(default_factory=list)
    pick_ids: List[str] = field(default_factory=list)
    skus: List[str] = field(default_factory=list)
    location_ids: List[str] = field(default_factory=list)
    distance_between_picks_m: List[Optional[float]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "order_id": self.order_id,
            "mode": self.mode,
            "complete": self.complete,
            "reason": self.reason,
            "total_distance_m": self.total_distance_m,
            "number_of_picks": self.number_of_picks,
            "number_of_lines": self.number_of_lines,
            "total_units": self.total_units,
            "zone_transitions": self.zone_transitions,
            "stop_nodes": list(self.stop_nodes),
            "distance_between_picks_m": list(self.distance_between_picks_m),
            "edge_traversals": dict(sorted(self.edge_traversals.items())),
            "unreachable_segments": list(self.unreachable_segments),
        }


class RoutingResult:
    """All routes for a dataset plus the aggregates every caller needs."""

    def __init__(self, routes: List[OrderRoute]) -> None:
        self.routes = routes

    @property
    def complete_routes(self) -> List[OrderRoute]:
        return [r for r in self.routes if r.complete and r.total_distance_m is not None]

    @property
    def incomplete_routes(self) -> List[OrderRoute]:
        return [r for r in self.routes if not r.complete]

    def total_distance_m(self) -> float:
        return sum(r.total_distance_m or 0.0 for r in self.complete_routes)

    def edge_traversals(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for route in self.complete_routes:
            for edge_id, count in route.edge_traversals.items():
                totals[edge_id] = totals.get(edge_id, 0) + count
        return totals

    def by_order(self) -> Dict[str, OrderRoute]:
        return {r.order_id: r for r in self.routes}


def _zone_of_node(dataset: Dataset, node_id: str) -> Optional[str]:
    node = dataset.nodes_by_id().get(node_id)
    return node.zone_id if node else None


def build_routes(
    dataset: Dataset,
    graph: Graph,
    location_override: Optional[Dict[str, str]] = None,
) -> RoutingResult:
    """Reconstruct every order route.

    ``location_override`` maps ``sku -> location_id`` and is how simulation
    replays history against a modified slotting without touching the dataset.
    """
    start_node = dataset.config.get("route_start_node")
    end_node = dataset.config.get("route_end_node")
    locations = dataset.locations_by_id()
    nodes = dataset.nodes_by_id()
    zone_by_node = {node_id: node.zone_id for node_id, node in nodes.items()}

    routes: List[OrderRoute] = []
    for order_id, picks in dataset.orders().items():
        route = _build_one_route(
            dataset=dataset,
            graph=graph,
            order_id=order_id,
            picks=picks,
            start_node=start_node,
            end_node=end_node,
            locations=locations,
            zone_by_node=zone_by_node,
            location_override=location_override or {},
        )
        routes.append(route)
    return RoutingResult(routes)


def _build_one_route(
    dataset: Dataset,
    graph: Graph,
    order_id: str,
    picks: Sequence[Pick],
    start_node: Optional[str],
    end_node: Optional[str],
    locations: Dict[str, object],
    zone_by_node: Dict[str, Optional[str]],
    location_override: Dict[str, str],
) -> OrderRoute:
    mode, mixed = dataset.order_mode(list(picks))
    route = OrderRoute(
        order_id=order_id,
        mode=mode,
        complete=False,
        reason=None,
        total_distance_m=None,
        number_of_picks=len(picks),
        number_of_lines=len(picks),
        total_units=sum(p.quantity for p in picks),
        pick_ids=[p.pick_id for p in picks],
        skus=[p.sku for p in picks],
    )

    if not dataset.order_has_sequence(list(picks)):
        route.reason = "MISSING_PICK_SEQUENCE"
        return route
    if mixed:
        route.reason = "MIXED_ORDER_MODES"
        return route
    if not start_node or not end_node:
        route.reason = "MISSING_ROUTE_ENDPOINTS"
        return route

    stops: List[Tuple[str, Optional[str]]] = [(start_node, None)]
    for pick in picks:
        location_id = location_override.get(pick.sku, pick.location_id)
        location = locations.get(location_id)
        if location is None:
            route.reason = "UNKNOWN_LOCATION"
            route.unreachable_segments.append(
                {"pick_id": pick.pick_id, "location_id": location_id, "reason": "UNKNOWN_LOCATION"}
            )
            return route
        stops.append((location.access_node_id, pick.pick_id))  # type: ignore[attr-defined]
        route.location_ids.append(location_id)
    stops.append((end_node, None))

    total = 0.0
    previous_zone = zone_by_node.get(start_node)
    zone_transitions = 0
    for index in range(1, len(stops)):
        from_node = stops[index - 1][0]
        to_node, to_pick_id = stops[index]
        path = graph.shortest_path(from_node, to_node, mode)
        if path is None:
            route.legs.append(RouteLeg(from_node, to_node, None, (), False, to_pick_id))
            route.unreachable_segments.append(
                {
                    "from_node": from_node,
                    "to_node": to_node,
                    "mode": mode,
                    "pick_id": to_pick_id or "",
                }
            )
            route.reason = "UNREACHABLE_SEGMENT"
            continue
        total += path.distance_m
        route.legs.append(
            RouteLeg(from_node, to_node, path.distance_m, path.edge_path, True, to_pick_id)
        )
        for edge_id in path.edge_path:
            route.edge_traversals[edge_id] = route.edge_traversals.get(edge_id, 0) + 1
        current_zone = zone_by_node.get(to_node)
        if current_zone != previous_zone:
            zone_transitions += 1
        previous_zone = current_zone

    route.stop_nodes = [node for node, _ in stops]
    route.zone_transitions = zone_transitions
    route.distance_between_picks_m = [leg.distance_m for leg in route.legs]

    if route.unreachable_segments:
        route.complete = False
        route.total_distance_m = None
        return route

    route.complete = True
    route.total_distance_m = round(total, 6)
    return route


def routes_touching_locations(
    result: RoutingResult, location_ids: Sequence[str]
) -> List[OrderRoute]:
    wanted = set(location_ids)
    return [r for r in result.routes if wanted.intersection(r.location_ids)]


def routes_touching_skus(result: RoutingResult, skus: Sequence[str]) -> List[OrderRoute]:
    wanted = set(skus)
    return [r for r in result.routes if wanted.intersection(r.skus)]
