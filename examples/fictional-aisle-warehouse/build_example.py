#!/usr/bin/env python3
"""Build the fictional aisle-warehouse dataset and the landing-page SVG.

The graph, distances, pick sequences and metrics come from the engine.
Racks, walls, docks and the office are a presentation overlay aligned to the
same coordinates. They are not inputs to Dijkstra.

Usage, from the repository root::

    python examples/fictional-aisle-warehouse/build_example.py
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from intralogistics_flow_analyzer.loaders import load_dataset, write_json
from intralogistics_flow_analyzer.reporting import analyse
from intralogistics_flow_analyzer.svg import (
    CANVAS_WIDTH,
    MARGIN,
    NODE_ROLE_LABELS,
    PALETTE,
    ROUTE_COLOURS,
    _Projection,
    _draw_node_marker,
    _node_role,
    _render_legend,
    _root,
    _route_points,
    _serialise,
    _text,
    select_routes,
)

HERE = Path(__file__).resolve().parent
DATASET_PATH = HERE / "dataset.json"
SVG_PATH = HERE / "spaghetti-landing.svg"
METRICS_PATH = HERE / "landing-metrics.json"

MODES = ["pedestrian", "forklift"]
AISLES = (("A", 10.0), ("B", 20.0), ("C", 30.0), ("D", 40.0), ("E", 50.0))
ACCESS_Y = (14.0, 22.0, 30.0)
NORTH_Y = 38.0
SOUTH_Y = 8.0
RECV = ("RECV", 30.0, 46.0)
PACK = ("PACK", 40.0, 3.0)
SHIP = ("SHIP", 30.0, 0.0)
OUT = ("OUT", 30.0, 3.0)
AISLE_HALF = 1.4
ROUTES_DRAWN = 8
# Extra metres so walls, docks and the office stay inside the SVG viewBox.
VIEW_PAD = (18.0, 16.0, 8.0, 8.0)  # west, east, south, north


def _edge(
    edge_id: str, a: str, b: str, distance_m: float
) -> Dict[str, Any]:
    return {
        "edge_id": edge_id,
        "from_node": a,
        "to_node": b,
        "distance_m": round(distance_m, 3),
        "bidirectional": True,
        "allowed_modes": list(MODES),
    }


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return abs(ax - bx) + abs(ay - by)


def build_document() -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    locations: List[Dict[str, Any]] = []
    by_id: Dict[str, Tuple[float, float]] = {}

    def add_node(node_id: str, x: float, y: float, node_type: str, zone: str) -> None:
        nodes.append(
            {
                "node_id": node_id,
                "x_m": x,
                "y_m": y,
                "node_type": node_type,
                "zone_id": zone,
            }
        )
        by_id[node_id] = (x, y)

    def connect(edge_id: str, a: str, b: str) -> None:
        ax, ay = by_id[a]
        bx, by = by_id[b]
        edges.append(_edge(edge_id, a, b, _dist(ax, ay, bx, by)))

    add_node(*RECV, "receiving", "Z-IN")
    add_node(*PACK, "packing", "Z-OUT")
    add_node(*SHIP, "shipping", "Z-OUT")
    add_node(*OUT, "junction", "Z-OUT")

    north_ids: List[str] = []
    south_ids: List[str] = []
    for letter, x in AISLES:
        north_id = f"{letter}N"
        south_id = f"{letter}S"
        add_node(north_id, x, NORTH_Y, "junction", "Z-PICK")
        add_node(south_id, x, SOUTH_Y, "junction", "Z-PICK")
        north_ids.append(north_id)
        south_ids.append(south_id)
        previous = north_id
        for y in reversed(ACCESS_Y):
            access_id = f"{letter}{int(y)}"
            add_node(access_id, x, y, "location_access", "Z-PICK")
            connect(f"E-{previous}-{access_id}", previous, access_id)
            previous = access_id
            chilled = letter == "E" and y == 30.0
            for side, suffix in (("L", "L"), ("R", "R")):
                loc_id = f"{letter}{int(y)}{suffix}"
                locations.append(
                    {
                        "location_id": loc_id,
                        "access_node_id": access_id,
                        "zone_id": "Z-CHILL" if chilled and suffix == "L" else "Z-PICK",
                        "location_type": "pick",
                        "capacity_volume_m3": 1.2,
                        "capacity_weight_kg": 250.0 if loc_id != "A22R" else 800.0,
                        "max_unit_weight_kg": 25.0 if loc_id != "A22R" else 80.0,
                        "temperature_zone": "chilled" if chilled and suffix == "L" else "ambient",
                        "hazard_classes_allowed": ["none"],
                        "allowed_modes": list(MODES),
                        "level": 1,
                        "fixed": loc_id == "B14L",
                    }
                )
        connect(f"E-{previous}-{south_id}", previous, south_id)

    for left, right in zip(north_ids, north_ids[1:]):
        connect(f"E-NC-{left}-{right}", left, right)
    for left, right in zip(south_ids, south_ids[1:]):
        connect(f"E-SC-{left}-{right}", left, right)

    connect("E-RECV-CN", "RECV", "CN")
    connect("E-DS-PACK", "DS", "PACK")
    connect("E-CS-SHIP", "CS", "SHIP")
    connect("E-PACK-OUT", "PACK", "OUT")
    connect("E-OUT-SHIP", "OUT", "SHIP")

    locations.append(
        {
            "location_id": "STAGING-IN",
            "access_node_id": "RECV",
            "zone_id": "Z-IN",
            "location_type": "staging",
            "capacity_volume_m3": 8.0,
            "capacity_weight_kg": 2000.0,
            "max_unit_weight_kg": 80.0,
            "temperature_zone": "ambient",
            "hazard_classes_allowed": ["none"],
            "allowed_modes": list(MODES),
            "level": 0,
            "fixed": True,
        }
    )
    locations.append(
        {
            "location_id": "STAGING-OUT",
            "access_node_id": "PACK",
            "zone_id": "Z-OUT",
            "location_type": "staging",
            "capacity_volume_m3": 6.0,
            "capacity_weight_kg": 1500.0,
            "max_unit_weight_kg": 80.0,
            "temperature_zone": "ambient",
            "hazard_classes_allowed": ["none"],
            "allowed_modes": list(MODES),
            "level": 0,
            "fixed": True,
        }
    )

    sku_map = [
        ("SKU-FAST-01", "A30L", "Fast-moving fastener kit", 0.02, 1.2, "ambient", False),
        ("SKU-FAST-02", "E30R", "Fast-moving packing tape", 0.03, 0.8, "ambient", False),
        ("SKU-FAST-03", "A14L", "Fast-moving label roll", 0.02, 0.6, "ambient", False),
        ("SKU-MID-01", "B22L", "Medium-runner gasket", 0.04, 2.0, "ambient", False),
        ("SKU-MID-02", "B22R", "Medium-runner clamp", 0.05, 2.4, "ambient", False),
        ("SKU-NEAR-01", "C14L", "Near-pack carton insert", 0.06, 1.5, "ambient", False),
        ("SKU-NEAR-02", "D14R", "Near-pack void fill", 0.08, 1.1, "ambient", False),
        ("SKU-MID-03", "C22L", "Medium-runner hose", 0.07, 3.2, "ambient", False),
        ("SKU-MID-04", "D22L", "Medium-runner elbow", 0.04, 1.8, "ambient", False),
        ("SKU-SLOW-01", "C30R", "Slow-mover spare cover", 0.09, 4.0, "ambient", False),
        ("SKU-CHILL-01", "E30L", "Chilled sample pack", 0.03, 1.0, "chilled", False),
        ("SKU-HEAVY-01", "A22R", "Heavy cast housing", 0.18, 42.0, "ambient", False),
        ("SKU-FIXED-01", "B14L", "Pinned safety stock", 0.05, 2.2, "ambient", True),
        ("SKU-EAST-01", "E14R", "East-aisle consumable", 0.03, 0.9, "ambient", False),
    ]
    items = []
    assignments = []
    for sku, location_id, description, volume, weight, temp, fixed in sku_map:
        items.append(
            {
                "sku": sku,
                "description": description,
                "unit_volume_m3": volume,
                "unit_weight_kg": weight,
                "temperature_zone": temp,
                "hazard_class": "none",
                "handling_mode": "forklift" if sku == "SKU-HEAVY-01" else "pedestrian",
                "fixed_location": fixed,
            }
        )
        assignments.append(
            {
                "sku": sku,
                "location_id": location_id,
                "role": "pick",
                "current_units": 20,
                "max_units": 80,
            }
        )

    location_of = {sku: location for sku, location, *_ in sku_map}
    order_skus = [
        ("ORD-2001", ["SKU-FAST-01", "SKU-FAST-02", "SKU-NEAR-02"]),
        ("ORD-2002", ["SKU-FAST-01", "SKU-EAST-01", "SKU-NEAR-02"]),
        ("ORD-2003", ["SKU-FAST-02", "SKU-FAST-03", "SKU-NEAR-01"]),
        ("ORD-2004", ["SKU-FAST-01", "SKU-FAST-02", "SKU-NEAR-01"]),
        ("ORD-2005", ["SKU-MID-01", "SKU-MID-02", "SKU-NEAR-02"]),
        ("ORD-2006", ["SKU-NEAR-01", "SKU-NEAR-02"]),
        ("ORD-2007", ["SKU-FAST-01", "SKU-MID-01", "SKU-EAST-01", "SKU-NEAR-02"]),
        ("ORD-2008", ["SKU-FAST-02", "SKU-MID-04", "SKU-NEAR-02"]),
        ("ORD-2009", ["SKU-FAST-01", "SKU-MID-03", "SKU-EAST-01"]),
        ("ORD-2010", ["SKU-MID-01", "SKU-NEAR-02"]),
        ("ORD-2011", ["SKU-FAST-01", "SKU-FAST-02", "SKU-MID-02", "SKU-NEAR-02"]),
        ("ORD-2012", ["SKU-NEAR-01", "SKU-NEAR-02"]),
        ("ORD-2013", ["SKU-FAST-03", "SKU-EAST-01", "SKU-NEAR-02"]),
        ("ORD-2014", ["SKU-FAST-01", "SKU-NEAR-02"]),
        ("ORD-2015", ["SKU-FAST-02", "SKU-NEAR-01", "SKU-NEAR-02"]),
        ("ORD-2016", ["SKU-MID-01", "SKU-MID-03", "SKU-EAST-01", "SKU-NEAR-02"]),
        ("ORD-2017", ["SKU-FAST-01", "SKU-SLOW-01", "SKU-NEAR-02"]),
        ("ORD-2018", ["SKU-FAST-02", "SKU-FAST-01", "SKU-MID-04", "SKU-NEAR-01"]),
    ]

    picks: List[Dict[str, Any]] = []
    pick_n = 1
    for order_index, (order_id, skus) in enumerate(order_skus):
        day = 2 + (order_index % 7)
        hour = 8 + (order_index % 5)
        for seq, sku in enumerate(skus, start=1):
            picks.append(
                {
                    "pick_id": f"PICK-{pick_n:04d}",
                    "order_id": order_id,
                    "pick_sequence": seq,
                    "timestamp": f"2026-04-{day:02d}T{hour:02d}:{seq * 7:02d}:00+02:00",
                    "sku": sku,
                    "location_id": location_of[sku],
                    "quantity": float(1 + (seq % 3)),
                    "mode": "pedestrian",
                }
            )
            pick_n += 1

    return {
        "schema_version": "1.0",
        "meta": {
            "dataset_id": "fictional-aisle-warehouse",
            "site_id": "SITE-FICTIONAL-AISLE",
            "period_start": "2026-04-02T00:00:00+02:00",
            "period_end": "2026-04-08T23:59:59+02:00",
            "distance_unit": "m",
            "time_zone": "Europe/Berlin",
            "source": (
                "Fictional aisle warehouse generated for a landing-page spaghetti "
                "diagram. Not derived from a commercial floor-plan image."
            ),
            "fictional": True,
            "sequence_basis": "planned",
            "distance_basis": "declared_graph_shortest_path",
        },
        "layout": {"nodes": nodes, "edges": edges},
        "locations": locations,
        "items": items,
        "assignments": assignments,
        "picks": picks,
        "config": {
            "route_start_node": "RECV",
            "route_end_node": "PACK",
            "default_mode": "pedestrian",
            "abc_basis": "pick_lines",
            "abc_thresholds": {"A": 0.8, "B": 0.95},
            "xyz_time_bucket": "day",
            "xyz_minimum_buckets": 7,
            "xyz_thresholds": {"X": 0.5, "Y": 1.0},
            "minimum_improvement_percent": 1.0,
            "maximum_recommendations": 10,
            "maximum_candidate_swaps": 500,
            "affinity_minimum_co_picks": 2,
            "affinity_minimum_support": 0.05,
            "restrict_swaps_to_same_zone": True,
            "allow_level_change": False,
            "spaghetti_top_orders": ROUTES_DRAWN,
        },
    }


def _rack_rects() -> List[Tuple[float, float, float, float, str]]:
    """Decorative rack footprints between aisle centre-lines."""
    rects: List[Tuple[float, float, float, float, str]] = []
    xs = [x for _, x in AISLES]
    y0, y1 = 10.5, 35.5
    for left, right in zip(xs, xs[1:]):
        inner_left = left + AISLE_HALF
        inner_right = right - AISLE_HALF
        mid = (inner_left + inner_right) / 2.0
        flue = 0.3
        rects.append((inner_left, y0, mid - flue / 2.0 - inner_left, y1 - y0, "Regal"))
        rects.append((mid + flue / 2.0, y0, inner_right - (mid + flue / 2.0), y1 - y0, "Regal"))
    west = xs[0] - AISLE_HALF - 3.6
    east = xs[-1] + AISLE_HALF + 0.2
    rects.append((west, y0, 3.4, y1 - y0, "Regal"))
    rects.append((east, y0, 3.4, y1 - y0, "Regal"))
    return rects


class _PaddedProjection(_Projection):
    """Same metre-to-pixel mapping as the engine, with room for the overlay."""

    def __init__(self, dataset, width: float = CANVAS_WIDTH, legend_rows: int = 5) -> None:
        super().__init__(dataset, width=width, legend_rows=legend_rows)
        west, east, south, north = VIEW_PAD
        self.min_x -= west
        self.max_x += east
        self.min_y -= south
        self.max_y += north
        span_x = max(self.max_x - self.min_x, 1.0)
        span_y = max(self.max_y - self.min_y, 1.0)
        self.scale = (width - 2 * MARGIN) / span_x
        self.height = span_y * self.scale + 2 * MARGIN + self.legend_height


def render_landing_svg(dataset, routing, selected) -> str:
    projection = _PaddedProjection(dataset, width=CANVAS_WIDTH, legend_rows=5)
    start_node = dataset.config.get("route_start_node")
    end_node = dataset.config.get("route_end_node")
    svg = _root(
        projection,
        "Modellierte Kommissionierwege in einem fiktiven Ganglayout",
        "Acht der längsten modellierten Auftragswege auf dem deklarierten "
        "Ganggraphen. Linien folgen Gängen und Öffnungen; sie kreuzen keine "
        "Regale. Regale, Wände, Tore und das Büro sind eine "
        "Darstellungsschicht und gehen nicht in die Distanzberechnung ein. "
        "Kein personenbezogener Datensatz.",
    )
    svg.set("width", "100%")
    svg.set("height", "auto")
    svg.set("preserveAspectRatio", "xMidYMid meet")

    architecture = ET.SubElement(svg, "g", {"id": "architecture-presentation"})
    ET.SubElement(
        architecture,
        "title",
    ).text = (
        "Presentation overlay: walls, racks, docks and office. Not used in distance calculation."
    )

    def box(x0: float, y0: float, w: float, h: float, **attrs: str) -> None:
        x, y_top = projection.point(x0, y0 + h)
        x2, y_bottom = projection.point(x0 + w, y0)
        ET.SubElement(
            architecture,
            "rect",
            {
                "x": f"{x:.2f}",
                "y": f"{y_top:.2f}",
                "width": f"{x2 - x:.2f}",
                "height": f"{y_bottom - y_top:.2f}",
                **attrs,
            },
        )

    def label(x_m: float, y_m: float, text: str, size: float = 11.0) -> None:
        x, y = projection.point(x_m, y_m)
        _text(
            architecture,
            x,
            y,
            text,
            size=size,
            fill=PALETTE["muted"],
            weight="bold",
            halo=True,
            anchor="middle",
        )

    box(-6.0, -6.0, 70.0, 58.0, fill="#f7f7f4", stroke=PALETTE["ink"], **{"stroke-width": "1.6"})
    box(-5.5, 40.0, 12.0, 11.0, fill="#ecece8", stroke=PALETTE["grid"], **{"stroke-width": "1"})
    label(0.5, 51.0, "Büro")
    box(8.0, 44.5, 44.0, 4.0, fill="#e4eee4", stroke=PALETTE["grid"], **{"stroke-width": "1"})
    label(30.0, 47.6, "Wareneingang")
    for dock_x in (16.0, 24.0, 32.0, 40.0):
        box(dock_x, 48.5, 4.0, 2.2, fill=PALETTE["surface"], stroke=PALETTE["ink"], **{"stroke-width": "1.2"})
    box(18.0, -5.0, 24.0, 4.2, fill="#e4eee4", stroke=PALETTE["grid"], **{"stroke-width": "1"})
    label(30.0, -2.4, "Versand")
    for dock_x in (20.0, 28.0, 36.0):
        box(dock_x, -5.0, 4.0, 2.0, fill=PALETTE["surface"], stroke=PALETTE["ink"], **{"stroke-width": "1.2"})
    box(34.0, 1.2, 14.0, 5.2, fill="#e8ece8", stroke=PALETTE["grid"], **{"stroke-width": "1"})
    label(41.0, 5.6, "Packerei")

    racks = ET.SubElement(architecture, "g", {"id": "racks"})
    for x0, y0, w, h, _name in _rack_rects():
        x, y_top = projection.point(x0, y0 + h)
        x2, y_bottom = projection.point(x0 + w, y0)
        ET.SubElement(
            racks,
            "rect",
            {
                "x": f"{x:.2f}",
                "y": f"{y_top:.2f}",
                "width": f"{x2 - x:.2f}",
                "height": f"{y_bottom - y_top:.2f}",
                "fill": "#d9ddd6",
                "stroke": "#8f948c",
                "stroke-width": "0.8",
            },
        )
    for letter, x in AISLES:
        label(x, 33.4, f"Gang {letter}", size=10)

    nodes = dataset.nodes_by_id()
    aisles = ET.SubElement(svg, "g", {"id": "aisle-graph"})
    for edge in dataset.edges:
        source = nodes.get(edge.from_node)
        target = nodes.get(edge.to_node)
        if source is None or target is None:
            continue
        x1, y1 = projection.point(source.x_m, source.y_m)
        x2, y2 = projection.point(target.x_m, target.y_m)
        ET.SubElement(
            aisles,
            "line",
            {
                "x1": f"{x1}",
                "y1": f"{y1}",
                "x2": f"{x2}",
                "y2": f"{y2}",
                "stroke": "#c5c8c2",
                "stroke-width": "7",
                "stroke-linecap": "round",
            },
        )

    route_layer = ET.SubElement(svg, "g", {"id": "modelled-routes"})
    legend_entries: List[Tuple[str, str, str]] = [
        ("Regal / Wand (Darstellung)", PALETTE["grid"], "box"),
        ("Begehbarer Gang (Graph)", "#c5c8c2", "line"),
    ]
    for index, route in enumerate(selected):
        colour = ROUTE_COLOURS[index % len(ROUTE_COLOURS)]
        offset = (index - (len(selected) - 1) / 2.0) * 2.4
        points = _route_points(dataset, route, projection, offset)
        if len(points) < 2:
            continue
        polyline = ET.SubElement(
            route_layer,
            "polyline",
            {
                "points": " ".join(f"{x:.2f},{y:.2f}" for x, y in points),
                "fill": "none",
                "stroke": colour,
                "stroke-width": "2.3",
                "stroke-opacity": "0.88",
                "stroke-linejoin": "round",
                "stroke-linecap": "round",
            },
        )
        ET.SubElement(polyline, "title").text = (
            f"{route.order_id}: {route.total_distance_m} m, {route.number_of_lines} Positionen"
        )
        legend_entries.append(
            (f"{route.order_id} · {route.total_distance_m} m", colour, "line")
        )

    node_layer = ET.SubElement(svg, "g", {"id": "nodes"})
    for node in sorted(dataset.nodes, key=lambda n: n.node_id):
        x, y = projection.point(node.x_m, node.y_m)
        role = _node_role(node, start_node, end_node)
        _draw_node_marker(
            node_layer,
            x,
            y,
            role,
            f"{node.node_id} ({NODE_ROLE_LABELS.get(role, role)})",
        )

    for node_id, caption in (("RECV", "Start"), ("PACK", "Ende")):
        node = nodes.get(node_id)
        if node is None:
            continue
        x, y = projection.point(node.x_m, node.y_m)
        _text(node_layer, x + 12, y - 8, caption, size=11, fill=PALETTE["ink"], weight="bold", halo=True)

    _render_legend(
        svg,
        projection,
        legend_entries[:12],
        (
            f"{len(selected)} der {len(routing.complete_routes)} modellierten Aufträge, "
            "die längsten zuerst. Distanzen: kürzester erlaubter Weg auf dem "
            "deklarierten Graphen. Fiktives Beispiel."
        ),
        fictional=False,
    )
    return _serialise(svg)


def main() -> int:
    document = build_document()
    write_json(DATASET_PATH, document)
    dataset = load_dataset(DATASET_PATH)
    result = analyse(dataset)
    metrics = result.analysis["metrics"]
    if result.analysis["status"] != "ok":
        print(json.dumps(result.analysis["issues"], indent=2), file=sys.stderr)
        return 1
    selected = select_routes(result.routing, top=ROUTES_DRAWN)
    SVG_PATH.write_text(render_landing_svg(dataset, result.routing, selected), encoding="utf-8")
    payload = {
        "dataset_id": dataset.meta.dataset_id,
        "sequence_basis": dataset.meta.sequence_basis,
        "distance_basis": dataset.meta.distance_basis,
        "orders_reconstructed": metrics["total_valid_orders"],
        "pick_lines": metrics["total_pick_lines"],
        "total_distance_m": metrics["total_distance_m"],
        "median_distance_per_order_m": metrics["median_distance_per_order_m"],
        "p90_distance_per_order_m": metrics["p90_distance_per_order_m"],
        "routes_drawn": len(selected),
        "routes_drawn_order_ids": [route.order_id for route in selected],
        "routes_drawn_distances_m": [route.total_distance_m for route in selected],
        "decorative_overlay": [
            "racks",
            "walls",
            "dock doors",
            "office",
            "zone labels",
        ],
        "calculated": [
            "order routes",
            "edge lengths",
            "order distances",
            "start and end nodes",
        ],
        "command": (
            "python examples/fictional-aisle-warehouse/build_example.py"
        ),
        "engine_analyze": (
            "ifa analyze --dataset examples/fictional-aisle-warehouse/dataset.json "
            "--output build/aisle-warehouse --spaghetti-top 8"
        ),
    }
    METRICS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"wrote {DATASET_PATH.relative_to(ROOT)}")
    print(f"wrote {SVG_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
