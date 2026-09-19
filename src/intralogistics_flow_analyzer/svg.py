"""SVG rendering with the standard library only.

Two drawings are produced:

``flow-map.svg``  nodes, aisle segments, zones, locations and depots, with edge
                  width and opacity scaled by traversal count, plus a legend.
``spaghetti.svg`` selected order routes drawn as offset polylines, limited to a
                  readable number of paths.

Both files carry ``<title>`` and ``<desc>`` so they stay accessible when
embedded in the HTML report, and both use a sober palette that reads on a light
background.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import Dataset
from .routing import OrderRoute, RoutingResult

PALETTE = {
    "accent": "#228B22",
    "ink": "#111111",
    "grid": "#D8D8D8",
    "muted": "#767676",
    "surface": "#FFFFFF",
    "zone": "#F2F2F2",
    "zone_alt": "#E8ECE8",
}

#: Deterministic colour cycle for spaghetti routes: dark neutrals plus the accent.
ROUTE_COLOURS = (
    "#228B22",
    "#111111",
    "#5A5A5A",
    "#2F6B2F",
    "#8A8A8A",
    "#1B5E1B",
    "#3C3C3C",
    "#4F8A4F",
    "#6E6E6E",
    "#0F3D0F",
)

MARGIN = 60.0
CANVAS_WIDTH = 900.0


def _bounds(dataset: Dataset) -> Tuple[float, float, float, float]:
    xs = [n.x_m for n in dataset.nodes] or [0.0]
    ys = [n.y_m for n in dataset.nodes] or [0.0]
    return min(xs), min(ys), max(xs), max(ys)


class _Projection:
    """Metres to canvas pixels, y flipped so north is up."""

    def __init__(
        self,
        dataset: Dataset,
        width: float = CANVAS_WIDTH,
        legend_rows: int = 3,
    ) -> None:
        self.min_x, self.min_y, self.max_x, self.max_y = _bounds(dataset)
        span_x = max(self.max_x - self.min_x, 1.0)
        span_y = max(self.max_y - self.min_y, 1.0)
        self.scale = (width - 2 * MARGIN) / span_x
        self.width = width
        # Reserve room for the legend rows plus the caption line underneath.
        self.legend_height = 46.0 + max(legend_rows, 1) * 18.0 + 20.0
        self.height = span_y * self.scale + 2 * MARGIN + self.legend_height

    def point(self, x_m: float, y_m: float) -> Tuple[float, float]:
        x = MARGIN + (x_m - self.min_x) * self.scale
        y = MARGIN + (self.max_y - y_m) * self.scale
        return round(x, 2), round(y, 2)


def _root(projection: _Projection, title: str, desc: str) -> ET.Element:
    svg = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {projection.width:.0f} {projection.height:.0f}",
            "width": f"{projection.width:.0f}",
            "height": f"{projection.height:.0f}",
            "role": "img",
            "font-family": "Helvetica, Arial, sans-serif",
        },
    )
    ET.SubElement(svg, "title").text = title
    ET.SubElement(svg, "desc").text = desc
    ET.SubElement(
        svg,
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": f"{projection.width:.0f}",
            "height": f"{projection.height:.0f}",
            "fill": PALETTE["surface"],
        },
    )
    return svg


def _text(
    parent: ET.Element,
    x: float,
    y: float,
    content: str,
    size: float = 11.0,
    fill: str = PALETTE["ink"],
    anchor: str = "start",
    weight: str = "normal",
    halo: bool = False,
) -> None:
    attributes = {
        "x": f"{x:.2f}",
        "y": f"{y:.2f}",
        "font-size": f"{size:.1f}",
        "fill": fill,
        "text-anchor": anchor,
        "font-weight": weight,
    }
    if halo:
        # A white outline painted behind the glyphs keeps a label readable where
        # it crosses an aisle segment or a route.
        attributes.update(
            {
                "stroke": PALETTE["surface"],
                "stroke-width": "3",
                "stroke-linejoin": "round",
                "paint-order": "stroke",
            }
        )
    element = ET.SubElement(parent, "text", attributes)
    element.text = content


def _serialise(svg: ET.Element) -> str:
    body = ET.tostring(svg, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


#: How each functional node type is drawn. Route start and route end are decided
#: at render time from ``config``; they take priority over the node's own type so
#: that the two ends of a tour are never mistaken for an ordinary dock node.
NODE_ROLE_STYLE = {
    "route_start": {"shape": "triangle", "fill": PALETTE["accent"], "radius": 8.0},
    "route_end": {"shape": "square", "fill": PALETTE["accent"], "radius": 7.0},
    "route_start_end": {"shape": "diamond", "fill": PALETTE["accent"], "radius": 8.5},
    "depot": {"shape": "circle", "fill": PALETTE["ink"], "radius": 7.0},
    "shipping": {"shape": "chevron", "fill": PALETTE["ink"], "radius": 7.0},
    "packing": {"shape": "square", "fill": PALETTE["ink"], "radius": 6.5},
    "receiving": {"shape": "chevron", "fill": PALETTE["muted"], "radius": 7.0},
    "location_access": {"shape": "circle", "fill": PALETTE["surface"], "radius": 4.5},
    "junction": {"shape": "circle", "fill": PALETTE["surface"], "radius": 4.0},
}

NODE_ROLE_LABELS = {
    "route_start": "Route start",
    "route_end": "Route end",
    "route_start_end": "Route start and end",
    "depot": "Depot",
    "shipping": "Shipping",
    "packing": "Packing",
    "receiving": "Receiving",
    "location_access": "Location access",
    "junction": "Junction",
}


def _node_role(node, start_node: Optional[str], end_node: Optional[str]) -> str:
    """Functional role of a node, with the tour endpoints taking priority."""
    if start_node and end_node and node.node_id == start_node == end_node:
        return "route_start_end"
    if start_node and node.node_id == start_node:
        return "route_start"
    if end_node and node.node_id == end_node:
        return "route_end"
    return node.node_type


def _draw_node_marker(
    parent: ET.Element, x: float, y: float, role: str, title: str
) -> None:
    """Draw one node with the marker shape that belongs to its role."""
    style = NODE_ROLE_STYLE.get(role, NODE_ROLE_STYLE["junction"])
    radius = float(style["radius"])
    shape = style["shape"]
    common = {
        "fill": str(style["fill"]),
        "stroke": PALETTE["ink"],
        "stroke-width": "1.4",
        "stroke-linejoin": "round",
    }
    if shape == "circle":
        element = ET.SubElement(
            parent, "circle", {"cx": f"{x:.2f}", "cy": f"{y:.2f}", "r": f"{radius:.2f}", **common}
        )
    elif shape == "square":
        element = ET.SubElement(
            parent,
            "rect",
            {
                "x": f"{x - radius:.2f}",
                "y": f"{y - radius:.2f}",
                "width": f"{2 * radius:.2f}",
                "height": f"{2 * radius:.2f}",
                "rx": "1.5",
                **common,
            },
        )
    elif shape == "triangle":
        points = f"{x:.2f},{y - radius:.2f} {x + radius:.2f},{y + radius:.2f} {x - radius:.2f},{y + radius:.2f}"
        element = ET.SubElement(parent, "polygon", {"points": points, **common})
    elif shape == "diamond":
        points = (
            f"{x:.2f},{y - radius:.2f} {x + radius:.2f},{y:.2f} "
            f"{x:.2f},{y + radius:.2f} {x - radius:.2f},{y:.2f}"
        )
        element = ET.SubElement(parent, "polygon", {"points": points, **common})
    else:  # chevron
        points = (
            f"{x - radius:.2f},{y - radius:.2f} {x:.2f},{y:.2f} "
            f"{x - radius:.2f},{y + radius:.2f} {x + radius:.2f},{y:.2f}"
        )
        element = ET.SubElement(parent, "polygon", {"points": points, **common})
    ET.SubElement(element, "title").text = title


def _arrow_marker_defs(svg: ET.Element) -> None:
    """A single arrowhead marker, used only on genuinely one-way edges."""
    defs = ET.SubElement(svg, "defs")
    marker = ET.SubElement(
        defs,
        "marker",
        {
            "id": "one-way-arrow",
            "viewBox": "0 0 10 10",
            "refX": "9",
            "refY": "5",
            "markerWidth": "5",
            "markerHeight": "5",
            "orient": "auto-start-reverse",
            "markerUnits": "strokeWidth",
        },
    )
    ET.SubElement(
        marker, "path", {"d": "M 0 0 L 10 5 L 0 10 z", "fill": PALETTE["ink"], "fill-opacity": "0.75"}
    )


def _zone_colour(zone_id: Optional[str], zones: Sequence[str]) -> str:
    if zone_id is None:
        return PALETTE["zone"]
    index = zones.index(zone_id) if zone_id in zones else 0
    return PALETTE["zone"] if index % 2 == 0 else PALETTE["zone_alt"]


def render_flow_map(
    dataset: Dataset,
    edge_rows: Sequence[Dict[str, Any]],
    location_rows: Sequence[Dict[str, Any]],
    fictional: bool = True,
) -> str:
    """Layout with aisle-segment traversal intensity and a legend."""
    projection = _Projection(dataset, legend_rows=3)
    traversals = {row["edge_id"]: int(row.get("traversals") or 0) for row in edge_rows}
    max_traversals = max(traversals.values()) if traversals else 0
    nodes = dataset.nodes_by_id()
    zones = sorted({n.zone_id for n in dataset.nodes if n.zone_id})
    start_node = dataset.config.get("route_start_node")
    end_node = dataset.config.get("route_end_node")

    svg = _root(
        projection,
        "Warehouse flow map",
        "Aisle graph of the analysed warehouse. Line width and opacity encode how often each "
        "segment is traversed in the modelled routes. Arrowheads mark one-way segments only. "
        "Node labels are layout node identifiers; no personal data is shown.",
    )

    zone_layer = ET.SubElement(svg, "g", {"id": "zones"})
    for zone in zones:
        zone_nodes = [n for n in dataset.nodes if n.zone_id == zone]
        if not zone_nodes:
            continue
        xs = [projection.point(n.x_m, n.y_m)[0] for n in zone_nodes]
        ys = [projection.point(n.x_m, n.y_m)[1] for n in zone_nodes]
        pad = 18.0
        ET.SubElement(
            zone_layer,
            "rect",
            {
                "x": f"{min(xs) - pad:.2f}",
                "y": f"{min(ys) - pad:.2f}",
                "width": f"{max(xs) - min(xs) + 2 * pad:.2f}",
                "height": f"{max(ys) - min(ys) + 2 * pad:.2f}",
                "rx": "10",
                "fill": _zone_colour(zone, zones),
                "fill-opacity": "0.6",
                "stroke": PALETTE["grid"],
                "stroke-width": "1",
                "stroke-dasharray": "5 4",
            },
        )
        # The label sits just above the rectangle: a single-aisle zone is only as
        # wide as its nodes, so anything drawn inside the top-left corner would
        # land on a node circle.
        _text(
            zone_layer,
            min(xs) - pad + 4,
            min(ys) - pad - 5,
            zone,
            size=11,
            fill=PALETTE["muted"],
            weight="bold",
            halo=True,
        )

    _arrow_marker_defs(svg)
    edge_layer = ET.SubElement(svg, "g", {"id": "aisle-segments"})
    has_directed_edge = False
    for edge in dataset.edges:
        source = nodes.get(edge.from_node)
        target = nodes.get(edge.to_node)
        if source is None or target is None:
            continue
        x1, y1 = projection.point(source.x_m, source.y_m)
        x2, y2 = projection.point(target.x_m, target.y_m)
        count = traversals.get(edge.edge_id, 0)
        ratio = (count / max_traversals) if max_traversals else 0.0
        width = 1.5 + 8.5 * ratio
        opacity = 0.25 + 0.65 * ratio
        colour = PALETTE["accent"] if count else PALETTE["grid"]
        attributes = {
            "x1": f"{x1}",
            "y1": f"{y1}",
            "x2": f"{x2}",
            "y2": f"{y2}",
            "stroke": colour,
            "stroke-width": f"{width:.2f}",
            "stroke-opacity": f"{opacity:.2f}",
            "stroke-linecap": "round",
        }
        # An arrowhead is drawn only where the edge really is one-way. A
        # bidirectional aisle gets none, so an arrow always means something.
        if not edge.bidirectional:
            attributes["marker-end"] = "url(#one-way-arrow)"
            has_directed_edge = True
        line = ET.SubElement(edge_layer, "line", attributes)
        direction = "one-way" if not edge.bidirectional else "two-way"
        ET.SubElement(line, "title").text = (
            f"{edge.edge_id}: {edge.from_node}-{edge.to_node} ({direction}), "
            f"{edge.distance_m:g} m, {count} modelled traversals"
        )

    location_layer = ET.SubElement(svg, "g", {"id": "locations"})
    by_node: Dict[str, List[Dict[str, Any]]] = {}
    for row in location_rows:
        by_node.setdefault(row["access_node_id"], []).append(row)
    for node_id, rows in sorted(by_node.items()):
        node = nodes.get(node_id)
        if node is None:
            continue
        base_x, base_y = projection.point(node.x_m, node.y_m)
        for index, row in enumerate(sorted(rows, key=lambda r: r["location_id"])):
            offset_x = base_x + 14 + 46 * (index % 2)
            offset_y = base_y - 16 + 16 * (index // 2)
            visits = int(row.get("visits") or 0)
            marker = ET.SubElement(
                location_layer,
                "rect",
                {
                    "x": f"{offset_x:.2f}",
                    "y": f"{offset_y:.2f}",
                    "width": "42",
                    "height": "13",
                    "rx": "3",
                    "fill": PALETTE["accent"] if visits else PALETTE["surface"],
                    "fill-opacity": "0.18" if visits else "1",
                    "stroke": PALETTE["muted"] if row["location_type"] == "pick" else PALETTE["grid"],
                    "stroke-width": "1",
                    "stroke-dasharray": "" if row["location_type"] == "pick" else "3 2",
                },
            )
            ET.SubElement(marker, "title").text = (
                f"{row['location_id']} ({row['location_type']}), {visits} visits, "
                f"{row.get('distance_from_start_node_m')} m from start node"
            )
            _text(
                location_layer,
                offset_x + 3,
                offset_y + 10,
                row["location_id"],
                size=8.5,
                fill=PALETTE["ink"],
            )

    node_layer = ET.SubElement(svg, "g", {"id": "nodes"})
    roles_present: List[str] = []
    for node in sorted(dataset.nodes, key=lambda n: n.node_id):
        x, y = projection.point(node.x_m, node.y_m)
        role = _node_role(node, start_node, end_node)
        if role not in roles_present:
            roles_present.append(role)
        _draw_node_marker(
            node_layer,
            x,
            y,
            role,
            f"{node.node_id} ({NODE_ROLE_LABELS.get(role, role)}, type {node.node_type})",
        )
        _text(
            node_layer,
            x - 11,
            y + 15,
            node.node_id,
            size=9.5,
            fill=PALETTE["muted"],
            anchor="end",
            halo=True,
        )

    legend_entries: List[Tuple[str, str, str]] = [
        ("Aisle segment, thickness = modelled traversals", PALETTE["accent"], "line"),
        ("Unused segment", PALETTE["grid"], "line"),
    ]
    if has_directed_edge:
        legend_entries.append(("One-way segment (arrow)", PALETTE["ink"], "arrow"))
    for role in (
        "route_start_end",
        "route_start",
        "route_end",
        "depot",
        "shipping",
        "packing",
        "receiving",
    ):
        if role in roles_present:
            legend_entries.append((NODE_ROLE_LABELS[role], role, "node"))
    legend_entries.append(("Junction or access node", "junction", "node"))
    legend_entries.append(("Pick location (shaded when visited)", PALETTE["accent"], "box"))
    legend_entries.append(("Reserve / buffer location", PALETTE["grid"], "box"))

    _render_legend(
        svg,
        projection,
        legend_entries,
        f"Maximum modelled traversals on a single segment: {max_traversals}.",
        fictional,
    )
    return _serialise(svg)


def _render_legend(
    svg: ET.Element,
    projection: _Projection,
    entries: Sequence[Tuple[str, str, str]],
    caption: str,
    fictional: bool,
) -> None:
    top = projection.height - projection.legend_height + 40.0
    legend = ET.SubElement(svg, "g", {"id": "legend"})
    ET.SubElement(
        legend,
        "line",
        {
            "x1": f"{MARGIN - 20:.2f}",
            "y1": f"{top - 16:.2f}",
            "x2": f"{projection.width - MARGIN + 20:.2f}",
            "y2": f"{top - 16:.2f}",
            "stroke": PALETTE["grid"],
            "stroke-width": "1",
        },
    )
    for index, (label, colour, shape) in enumerate(entries):
        column = index % 2
        row = index // 2
        x = MARGIN - 20 + column * 390
        y = top + row * 18
        if shape == "line":
            ET.SubElement(
                legend,
                "line",
                {
                    "x1": f"{x:.2f}",
                    "y1": f"{y:.2f}",
                    "x2": f"{x + 22:.2f}",
                    "y2": f"{y:.2f}",
                    "stroke": colour,
                    "stroke-width": "4",
                    "stroke-linecap": "round",
                },
            )
        elif shape == "dot":
            ET.SubElement(
                legend,
                "circle",
                {
                    "cx": f"{x + 11:.2f}",
                    "cy": f"{y:.2f}",
                    "r": "5",
                    "fill": colour,
                    "stroke": PALETTE["ink"],
                    "stroke-width": "1.2",
                },
            )
        elif shape == "arrow":
            ET.SubElement(
                legend,
                "line",
                {
                    "x1": f"{x:.2f}",
                    "y1": f"{y:.2f}",
                    "x2": f"{x + 18:.2f}",
                    "y2": f"{y:.2f}",
                    "stroke": colour,
                    "stroke-width": "2",
                    "marker-end": "url(#one-way-arrow)",
                },
            )
        elif shape == "node":
            # ``colour`` carries the role key for node samples.
            _draw_node_marker(legend, x + 11, y, colour, "")
        else:
            ET.SubElement(
                legend,
                "rect",
                {
                    "x": f"{x:.2f}",
                    "y": f"{y - 5:.2f}",
                    "width": "22",
                    "height": "10",
                    "rx": "2",
                    "fill": colour,
                    "fill-opacity": "0.25",
                    "stroke": PALETTE["muted"],
                    "stroke-width": "1",
                },
            )
        _text(legend, x + 30, y + 4, label, size=10.5, fill=PALETTE["ink"])

    rows = (len(entries) + 1) // 2
    note = caption
    if fictional:
        note = (note + "  " if note else "") + "Fictional example dataset."
    if note:
        _text(legend, MARGIN - 20, top + rows * 18 + 6, note, size=10, fill=PALETTE["muted"])


def select_routes(
    routing: RoutingResult,
    order_id: Optional[str] = None,
    top: Optional[int] = None,
) -> List[OrderRoute]:
    """Pick which routes to draw: one order, the N longest, or the N longest by default."""
    complete = routing.complete_routes
    if order_id:
        return [r for r in complete if r.order_id == order_id]
    ordered = sorted(complete, key=lambda r: (-(r.total_distance_m or 0.0), r.order_id))
    return ordered[: max(1, int(top or 10))]


def render_spaghetti(
    dataset: Dataset,
    routing: RoutingResult,
    order_id: Optional[str] = None,
    top: Optional[int] = None,
    aggregate: bool = False,
    fictional: bool = True,
) -> str:
    """Spaghetti diagram of selected routes, or of the aggregated flow.

    Thousands of overlapping polylines are unreadable, so the number of drawn
    routes is capped and each route is drawn with a small deterministic offset so
    that shared segments stay distinguishable.
    """
    nodes = dataset.nodes_by_id()
    selected = [] if aggregate else select_routes(routing, order_id, top)
    legend_rows = 1 if aggregate else max(1, (min(len(selected), 12) + 1) // 2)
    projection = _Projection(dataset, legend_rows=legend_rows)

    if aggregate:
        title = "Aggregated modelled picking flow"
        description = (
            "All modelled routes collapsed into per-segment traversal counts. "
            "Line width encodes how often a segment was walked."
        )
    elif order_id:
        title = f"Modelled picking route of order {order_id}"
        description = (
            f"Modelled route of order {order_id}: the shortest permitted path on the declared "
            "aisle graph between consecutive pick access nodes, in the given sequence."
        )
    else:
        title = f"Longest {len(selected)} modelled picking routes"
        description = (
            f"The {len(selected)} longest modelled routes, drawn with a small offset per "
            "route so overlapping segments stay readable."
        )

    svg = _root(projection, title, description + " No personal data is shown.")

    _arrow_marker_defs(svg)
    base_layer = ET.SubElement(svg, "g", {"id": "layout"})
    for edge in dataset.edges:
        source = nodes.get(edge.from_node)
        target = nodes.get(edge.to_node)
        if source is None or target is None:
            continue
        x1, y1 = projection.point(source.x_m, source.y_m)
        x2, y2 = projection.point(target.x_m, target.y_m)
        attributes = {
            "x1": f"{x1}",
            "y1": f"{y1}",
            "x2": f"{x2}",
            "y2": f"{y2}",
            "stroke": PALETTE["grid"],
            "stroke-width": "2",
            "stroke-linecap": "round",
        }
        if not edge.bidirectional:
            attributes["marker-end"] = "url(#one-way-arrow)"
        ET.SubElement(base_layer, "line", attributes)

    legend_entries: List[Tuple[str, str, str]] = []
    if aggregate:
        counts = routing.edge_traversals()
        maximum = max(counts.values()) if counts else 0
        flow_layer = ET.SubElement(svg, "g", {"id": "aggregated-flow"})
        for edge in dataset.edges:
            count = counts.get(edge.edge_id, 0)
            if not count:
                continue
            source = nodes.get(edge.from_node)
            target = nodes.get(edge.to_node)
            if source is None or target is None:
                continue
            x1, y1 = projection.point(source.x_m, source.y_m)
            x2, y2 = projection.point(target.x_m, target.y_m)
            ratio = count / maximum if maximum else 0.0
            line = ET.SubElement(
                flow_layer,
                "line",
                {
                    "x1": f"{x1}",
                    "y1": f"{y1}",
                    "x2": f"{x2}",
                    "y2": f"{y2}",
                    "stroke": PALETTE["accent"],
                    "stroke-width": f"{1.5 + 9.0 * ratio:.2f}",
                    "stroke-opacity": f"{0.3 + 0.6 * ratio:.2f}",
                    "stroke-linecap": "round",
                },
            )
            ET.SubElement(line, "title").text = (
                f"{edge.edge_id}: {count} modelled traversals"
            )
        legend_entries.append(("Modelled traversal intensity", PALETTE["accent"], "line"))
    else:
        route_layer = ET.SubElement(svg, "g", {"id": "routes"})
        for index, route in enumerate(selected):
            colour = ROUTE_COLOURS[index % len(ROUTE_COLOURS)]
            offset = (index - (len(selected) - 1) / 2.0) * 2.6
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
                    "stroke-width": "2.2",
                    "stroke-opacity": "0.85",
                    "stroke-linejoin": "round",
                    "stroke-linecap": "round",
                },
            )
            ET.SubElement(polyline, "title").text = (
                f"{route.order_id}: {route.total_distance_m} m, {route.number_of_lines} lines, "
                f"mode {route.mode}"
            )
            legend_entries.append(
                (f"{route.order_id} ({route.total_distance_m} m)", colour, "line")
            )

    node_layer = ET.SubElement(svg, "g", {"id": "nodes"})
    start_node = dataset.config.get("route_start_node")
    end_node = dataset.config.get("route_end_node")
    for node in sorted(dataset.nodes, key=lambda n: n.node_id):
        x, y = projection.point(node.x_m, node.y_m)
        role = _node_role(node, start_node, end_node)
        _draw_node_marker(
            node_layer,
            x,
            y,
            role,
            f"{node.node_id} ({NODE_ROLE_LABELS.get(role, role)}, type {node.node_type})",
        )
        _text(
            node_layer,
            x - 10,
            y + 14,
            node.node_id,
            size=9,
            fill=PALETTE["muted"],
            anchor="end",
            halo=True,
        )

    if aggregate:
        counts = routing.edge_traversals()
        caption = (
            "Maximum modelled traversals on a single segment: "
            f"{max(counts.values()) if counts else 0}."
        )
    elif order_id:
        caption = (
            "One modelled route. Routes are offset slightly so shared segments stay visible."
        )
    else:
        caption = (
            f"{len(selected)} of {len(routing.complete_routes)} modelled routes, "
            "the longest first. Routes are offset slightly so shared segments stay visible."
        )
    _render_legend(svg, projection, legend_entries[:12], caption, fictional)
    return _serialise(svg)


def _route_points(
    dataset: Dataset,
    route: OrderRoute,
    projection: _Projection,
    offset: float,
) -> List[Tuple[float, float]]:
    nodes = dataset.nodes_by_id()
    node_sequence: List[str] = []
    for leg in route.legs:
        if not leg.reachable:
            continue
        path = _expand_leg(dataset, leg)
        for node_id in path:
            if not node_sequence or node_sequence[-1] != node_id:
                node_sequence.append(node_id)
    points: List[Tuple[float, float]] = []
    for index, node_id in enumerate(node_sequence):
        node = nodes.get(node_id)
        if node is None:
            continue
        x, y = projection.point(node.x_m, node.y_m)
        normal_x, normal_y = _offset_normal(node_sequence, index, dataset, projection)
        points.append((x + normal_x * offset, y + normal_y * offset))
    return points


def _expand_leg(dataset: Dataset, leg) -> List[str]:
    """Node sequence of a leg, reconstructed from its edge identifiers."""
    edges = {e.edge_id: e for e in dataset.edges}
    sequence = [leg.from_node]
    cursor = leg.from_node
    for edge_id in leg.edge_path:
        edge = edges.get(edge_id)
        if edge is None:
            break
        nxt = edge.to_node if edge.from_node == cursor else edge.from_node
        sequence.append(nxt)
        cursor = nxt
    if sequence[-1] != leg.to_node:
        sequence.append(leg.to_node)
    return sequence


def _offset_normal(
    sequence: Sequence[str], index: int, dataset: Dataset, projection: _Projection
) -> Tuple[float, float]:
    nodes = dataset.nodes_by_id()
    previous = sequence[index - 1] if index > 0 else sequence[index]
    following = sequence[index + 1] if index + 1 < len(sequence) else sequence[index]
    a = nodes.get(previous)
    b = nodes.get(following)
    if a is None or b is None:
        return 0.0, 0.0
    ax, ay = projection.point(a.x_m, a.y_m)
    bx, by = projection.point(b.x_m, b.y_m)
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length == 0:
        return 0.0, 0.0
    return -dy / length, dx / length
