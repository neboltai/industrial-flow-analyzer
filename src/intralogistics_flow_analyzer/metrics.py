"""Flow metrics computed from reconstructed routes.

Only complete routes contribute to distance metrics. Orders with an unreachable
segment or a missing pick sequence are counted separately and never smoothed
into an average.

Percentile definition (deterministic, documented, no third-party library):

    sorted ascending, rank = p/100 * (n - 1), linear interpolation between the
    two neighbouring ranks. For n == 1 the single value is returned.

This is the "linear interpolation between closest ranks" method. It is stated in
the report so that any number can be recomputed by hand.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence

from .graph import Graph
from .models import Dataset
from .routing import OrderRoute, RoutingResult


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    """Linear-interpolation percentile. ``p`` is expressed in percent."""
    if not values:
        return None
    if not 0.0 <= p <= 100.0:
        raise ValueError("percentile p must be within [0, 100]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100.0) * (len(ordered) - 1)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = rank - lower_index
    lower = float(ordered[lower_index])
    upper = float(ordered[upper_index])
    return lower + (upper - lower) * fraction


def _round(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def compute_metrics(
    dataset: Dataset, routing: RoutingResult, graph: Optional[Graph] = None
) -> Dict[str, Any]:
    """Aggregate flow metrics for the whole dataset."""
    complete = routing.complete_routes
    distances = [r.total_distance_m for r in complete if r.total_distance_m is not None]

    total_pick_lines = len(dataset.picks)
    total_units = sum(p.quantity for p in dataset.picks)
    lines_in_complete = sum(r.number_of_lines for r in complete)
    total_distance = sum(distances)

    metrics: Dict[str, Any] = {
        "total_orders": len(dataset.orders()),
        "total_valid_orders": len(complete),
        "total_pick_lines": total_pick_lines,
        "pick_lines_in_valid_orders": lines_in_complete,
        "total_units": _round(total_units),
        "total_distance_m": _round(total_distance),
        "average_distance_per_order_m": _round(
            statistics.fmean(distances) if distances else None
        ),
        "median_distance_per_order_m": _round(statistics.median(distances) if distances else None),
        "p90_distance_per_order_m": _round(percentile(distances, 90.0)),
        "average_distance_per_pick_m": _round(
            (total_distance / lines_in_complete) if lines_in_complete else None
        ),
        "zone_transitions": sum(r.zone_transitions for r in complete),
        "unreachable_order_count": len(
            [r for r in routing.routes if r.reason == "UNREACHABLE_SEGMENT"]
        ),
        "unsequenced_order_count": len(
            [r for r in routing.routes if r.reason == "MISSING_PICK_SEQUENCE"]
        ),
        "excluded_order_count": len(routing.incomplete_routes),
        "percentile_method": "linear interpolation between closest ranks",
    }
    return metrics


def location_metrics(dataset: Dataset, routing: RoutingResult, graph: Graph) -> List[Dict[str, Any]]:
    """Per-location visit frequency and attributable travel distance.

    Distance attribution rule (documented, and repeated in the report): the leg
    that *arrives* at a location's access node is charged to that location. The
    return leg to the end node is charged to the last location of the order.
    This is an accounting convention, not a physical measurement.
    """
    locations = dataset.locations_by_id()
    start_node = dataset.config.get("route_start_node")
    default_mode = str(dataset.config.get("default_mode") or "pedestrian")

    stats: Dict[str, Dict[str, Any]] = {}
    for location_id, location in locations.items():
        depot_distance = (
            graph.distance(str(start_node), location.access_node_id, default_mode)
            if start_node
            else None
        )
        stats[location_id] = {
            "location_id": location_id,
            "zone_id": location.zone_id,
            "location_type": location.location_type,
            "access_node_id": location.access_node_id,
            "distance_from_start_node_m": _round(depot_distance),
            "visits": 0,
            "pick_lines": 0,
            "units": 0.0,
            "attributable_distance_m": 0.0,
            "orders": 0,
        }

    orders_seen: Dict[str, set] = {loc: set() for loc in locations}
    for route in routing.complete_routes:
        # legs[0] arrives at the first pick, legs[i] arrives at pick i, the final
        # leg returns to the end node.
        for index, location_id in enumerate(route.location_ids):
            entry = stats.get(location_id)
            if entry is None:
                continue
            entry["visits"] += 1
            entry["pick_lines"] += 1
            orders_seen.setdefault(location_id, set()).add(route.order_id)
            arriving = route.legs[index] if index < len(route.legs) else None
            if arriving and arriving.distance_m is not None:
                entry["attributable_distance_m"] += arriving.distance_m
            if index == len(route.location_ids) - 1 and len(route.legs) == len(
                route.location_ids
            ) + 1:
                return_leg = route.legs[-1]
                if return_leg.distance_m is not None:
                    entry["attributable_distance_m"] += return_leg.distance_m

    for pick in dataset.picks:
        entry = stats.get(pick.location_id)
        if entry is not None:
            entry["units"] += pick.quantity

    rows: List[Dict[str, Any]] = []
    total_visits = sum(e["visits"] for e in stats.values()) or 1
    total_distance = sum(e["attributable_distance_m"] for e in stats.values()) or 1.0
    for location_id in sorted(stats):
        entry = stats[location_id]
        entry["orders"] = len(orders_seen.get(location_id, set()))
        entry["units"] = _round(entry["units"])
        entry["attributable_distance_m"] = _round(entry["attributable_distance_m"])
        entry["visit_share_percent"] = _round(100.0 * entry["visits"] / total_visits, 2)
        entry["distance_share_percent"] = _round(
            100.0 * (entry["attributable_distance_m"] or 0.0) / total_distance, 2
        )
        rows.append(entry)
    return rows


def sku_metrics(dataset: Dataset, routing: RoutingResult, graph: Graph) -> List[Dict[str, Any]]:
    """Per-SKU pick frequency, units and attributable travel distance."""
    location_rows = {row["location_id"]: row for row in location_metrics(dataset, routing, graph)}
    pick_location = dataset.pick_location_of()
    items = dataset.items_by_sku()

    stats: Dict[str, Dict[str, Any]] = {}
    for sku, item in items.items():
        stats[sku] = {
            "sku": sku,
            "description": item.description,
            "pick_location_id": pick_location.get(sku),
            "pick_lines": 0,
            "units": 0.0,
            "orders": 0,
            "attributable_distance_m": 0.0,
        }

    orders_seen: Dict[str, set] = {sku: set() for sku in items}
    for pick in dataset.picks:
        entry = stats.get(pick.sku)
        if entry is None:
            continue
        entry["pick_lines"] += 1
        entry["units"] += pick.quantity
        orders_seen.setdefault(pick.sku, set()).add(pick.order_id)

    for route in routing.complete_routes:
        for index, sku in enumerate(route.skus):
            entry = stats.get(sku)
            if entry is None:
                continue
            leg = route.legs[index] if index < len(route.legs) else None
            if leg and leg.distance_m is not None:
                entry["attributable_distance_m"] += leg.distance_m

    total_lines = sum(e["pick_lines"] for e in stats.values()) or 1
    rows: List[Dict[str, Any]] = []
    for sku in sorted(stats):
        entry = stats[sku]
        entry["orders"] = len(orders_seen.get(sku, set()))
        entry["units"] = _round(entry["units"])
        entry["attributable_distance_m"] = _round(entry["attributable_distance_m"])
        entry["pick_line_share_percent"] = _round(100.0 * entry["pick_lines"] / total_lines, 2)
        location_row = location_rows.get(entry["pick_location_id"] or "")
        entry["distance_from_start_node_m"] = (
            location_row["distance_from_start_node_m"] if location_row else None
        )
        rows.append(entry)
    return rows


def edge_flows(dataset: Dataset, routing: RoutingResult) -> List[Dict[str, Any]]:
    """Traversal count per edge, with the share of total traversals."""
    traversals = routing.edge_traversals()
    total = sum(traversals.values()) or 1
    rows: List[Dict[str, Any]] = []
    for edge in dataset.edges:
        count = traversals.get(edge.edge_id, 0)
        rows.append(
            {
                "edge_id": edge.edge_id,
                "from_node": edge.from_node,
                "to_node": edge.to_node,
                "distance_m": edge.distance_m,
                "traversals": count,
                "traversal_share_percent": _round(100.0 * count / total, 2),
                "travelled_distance_m": _round(count * edge.distance_m),
            }
        )
    rows.sort(key=lambda r: (-r["traversals"], r["edge_id"]))
    return rows


def zone_flows(dataset: Dataset, routing: RoutingResult) -> List[Dict[str, Any]]:
    """Visit counts per zone, derived from the locations actually visited."""
    locations = dataset.locations_by_id()
    counts: Dict[str, int] = {}
    for route in routing.complete_routes:
        for location_id in route.location_ids:
            location = locations.get(location_id)
            zone = (location.zone_id if location else None) or "UNZONED"
            counts[zone] = counts.get(zone, 0) + 1
    total = sum(counts.values()) or 1
    rows = [
        {
            "zone_id": zone,
            "visits": count,
            "visit_share_percent": _round(100.0 * count / total, 2),
        }
        for zone, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return rows


def top_orders_by_distance(routing: RoutingResult, limit: int = 10) -> List[Dict[str, Any]]:
    ordered = sorted(
        routing.complete_routes,
        key=lambda r: (-(r.total_distance_m or 0.0), r.order_id),
    )
    return [
        {
            "order_id": route.order_id,
            "total_distance_m": route.total_distance_m,
            "number_of_lines": route.number_of_lines,
            "zone_transitions": route.zone_transitions,
            "mode": route.mode,
        }
        for route in ordered[:limit]
    ]


def top_locations_by_distance(rows: Sequence[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda r: (-(r.get("attributable_distance_m") or 0.0), r["location_id"]),
    )
    return [row for row in ordered[:limit] if (row.get("attributable_distance_m") or 0.0) > 0]


def data_quality(dataset: Dataset, routing: RoutingResult) -> Dict[str, Any]:
    total_orders = len(dataset.orders())
    complete = len(routing.complete_routes)
    return {
        "orders_total": total_orders,
        "orders_routed": complete,
        "orders_excluded": total_orders - complete,
        "route_coverage_percent": _round(
            100.0 * complete / total_orders if total_orders else 0.0, 2
        ),
        "picks_total": len(dataset.picks),
        "picks_with_sequence": len([p for p in dataset.picks if p.pick_sequence is not None]),
        "picks_with_timestamp": len([p for p in dataset.picks if p.timestamp]),
        "locations_total": len(dataset.locations),
        "pick_locations_total": len(
            [l for l in dataset.locations if l.location_type == "pick"]
        ),
        "items_total": len(dataset.items),
        "nodes_total": len(dataset.nodes),
        "edges_total": len(dataset.edges),
    }
