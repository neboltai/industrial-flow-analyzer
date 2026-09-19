"""Deterministic dataset validation.

Nothing is repaired silently. Every problem becomes an :class:`Issue` with a
stable ``code``, a ``severity``, the ``field`` it belongs to and the concrete
records it affects.

Status is derived, never asserted:

``data_error``            at least one blocking error
``insufficient_evidence`` no order can be routed at all
``partial``              some orders are routable, some are not
``ok``                   every order is routable
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from .graph import Graph, build_graph
from .models import (
    ANALYSIS_STATUSES,
    DISTANCE_BASES,
    Dataset,
    Issue,
    LOCATION_TYPES,
    MODES,
    SEQUENCE_BASES,
)
from .routing import RoutingResult, build_routes

EXIT_OK = 0
EXIT_BLOCKING = 1
EXIT_PARTIAL = 2

#: Error codes that make the dataset structurally unusable. Any of them forces
#: ``data_error`` and stops the engine before routing. Errors outside this set
#: (a single unreachable order, for instance) degrade the status to ``partial``
#: but still allow the remaining evidence to be analysed.
STRUCTURAL_CODES = frozenset(
    {
        "DUPLICATE_NODE_ID",
        "DUPLICATE_EDGE_ID",
        "DUPLICATE_LOCATION_ID",
        "DUPLICATE_SKU",
        "DUPLICATE_PICK_ID",
        "UNKNOWN_NODE_REFERENCE",
        "UNKNOWN_SKU",
        "UNKNOWN_LOCATION",
        "INVALID_EDGE_DISTANCE",
        "EDGE_WITHOUT_MODE",
        "DISCONNECTED_GRAPH",
        "EMPTY_LAYOUT",
        "NO_PICKS",
        "INVALID_QUANTITY",
        "INVALID_CAPACITY",
        "INCONSISTENT_CAPACITY",
        "INVALID_ITEM_MEASURE",
        "DUPLICATE_PICK_SEQUENCE",
        "INVALID_PICK_SEQUENCE",
        "MULTIPLE_PICK_ASSIGNMENTS",
        "SHARED_PICK_LOCATION",
        "TEMPERATURE_INCOMPATIBLE",
        "HAZARD_INCOMPATIBLE",
        "MODE_NOT_ALLOWED",
        "ITEM_TOO_HEAVY_FOR_LOCATION",
        "LOCATION_WITHOUT_ACCESS_NODE",
        "MISSING_ROUTE_ENDPOINT",
        "MISSING_ORDER_ID",
        "UNKNOWN_DISTANCE_UNIT",
        "UNKNOWN_SEQUENCE_BASIS",
        "UNKNOWN_DISTANCE_BASIS",
        "UNKNOWN_MODE",
        "UNKNOWN_ABC_BASIS",
        "INVALID_ABC_THRESHOLDS",
        "INVALID_TIMESTAMP",
        "TIMESTAMP_OUTSIDE_PERIOD",
        "PICK_ROLE_ON_NON_PICK_LOCATION",
        "UNKNOWN_LOCATION_TYPE",
    }
)


@dataclass
class ValidationReport:
    status: str
    issues: List[Issue] = field(default_factory=list)
    routable_orders: int = 0
    unroutable_orders: int = 0
    total_orders: int = 0

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    def exit_code(self) -> int:
        if self.status == "data_error":
            return EXIT_BLOCKING
        if self.status in ("partial", "insufficient_evidence"):
            return EXIT_PARTIAL
        return EXIT_OK

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "total_orders": self.total_orders,
            "routable_orders": self.routable_orders,
            "unroutable_orders": self.unroutable_orders,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class _Collector:
    def __init__(self) -> None:
        self.issues: List[Issue] = []

    def add(
        self,
        code: str,
        severity: str,
        field_name: str,
        message: str,
        affected: Optional[Sequence[str]] = None,
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                severity=severity,
                field=field_name,
                message=message,
                affected_records=list(affected or []),
            )
        )


def _check_meta(dataset: Dataset, out: _Collector) -> None:
    meta = dataset.meta
    if meta.sequence_basis not in SEQUENCE_BASES:
        out.add(
            "UNKNOWN_SEQUENCE_BASIS",
            "error",
            "meta.sequence_basis",
            f"meta.sequence_basis {meta.sequence_basis!r} is not one of "
            f"{', '.join(SEQUENCE_BASES)}.",
            [meta.dataset_id],
        )
    elif meta.sequence_basis == "unknown":
        out.add(
            "SEQUENCE_BASIS_UNKNOWN",
            "info",
            "meta.sequence_basis",
            "meta.sequence_basis is 'unknown'. Route distances are modelled from the declared "
            "pick sequence, which may be a plan rather than the order actually walked. Declare "
            "'planned', 'scan_confirmed' or 'observed' to state the provenance.",
            [meta.dataset_id],
        )
    if meta.distance_basis not in DISTANCE_BASES:
        out.add(
            "UNKNOWN_DISTANCE_BASIS",
            "error",
            "meta.distance_basis",
            f"meta.distance_basis {meta.distance_basis!r} is not supported in schema 1.0 "
            f"(supported: {', '.join(DISTANCE_BASES)}).",
            [meta.dataset_id],
        )
    if meta.distance_unit != "m":
        out.add(
            "UNKNOWN_DISTANCE_UNIT",
            "error",
            "meta.distance_unit",
            f"distance_unit must be 'm' in schema 1.0, found {meta.distance_unit!r}.",
            [meta.dataset_id],
        )
    for name in ("period_start", "period_end"):
        raw = getattr(meta, name)
        if raw and _parse_iso(raw) is None:
            out.add(
                "INVALID_TIMESTAMP",
                "error",
                f"meta.{name}",
                f"meta.{name} is not an ISO 8601 timestamp: {raw!r}.",
                [meta.dataset_id],
            )
    if not meta.fictional:
        out.add(
            "NON_FICTIONAL_DATASET",
            "info",
            "meta.fictional",
            "Dataset is not flagged as fictional. Confirm it contains no personal data "
            "before sharing any report produced from it.",
            [meta.dataset_id],
        )


def _check_duplicates(dataset: Dataset, out: _Collector) -> None:
    checks = (
        ("layout.nodes", "node_id", [n.node_id for n in dataset.nodes], "DUPLICATE_NODE_ID"),
        ("layout.edges", "edge_id", [e.edge_id for e in dataset.edges], "DUPLICATE_EDGE_ID"),
        (
            "locations",
            "location_id",
            [l.location_id for l in dataset.locations],
            "DUPLICATE_LOCATION_ID",
        ),
        ("items", "sku", [i.sku for i in dataset.items], "DUPLICATE_SKU"),
        ("picks", "pick_id", [p.pick_id for p in dataset.picks], "DUPLICATE_PICK_ID"),
    )
    for field_name, key, values, code in checks:
        seen: Dict[str, int] = {}
        for value in values:
            seen[value] = seen.get(value, 0) + 1
        duplicates = sorted(v for v, count in seen.items() if count > 1)
        if duplicates:
            out.add(
                code,
                "error",
                field_name,
                f"Duplicate {key} in {field_name}: {', '.join(duplicates)}.",
                duplicates,
            )


def _check_layout(dataset: Dataset, graph: Graph, out: _Collector) -> None:
    node_ids = {n.node_id for n in dataset.nodes}
    if not dataset.nodes:
        out.add("EMPTY_LAYOUT", "error", "layout.nodes", "Layout contains no nodes.", [])
    if not dataset.edges:
        out.add("EMPTY_LAYOUT", "error", "layout.edges", "Layout contains no edges.", [])

    for edge in dataset.edges:
        missing = [n for n in (edge.from_node, edge.to_node) if n not in node_ids]
        if missing:
            out.add(
                "UNKNOWN_NODE_REFERENCE",
                "error",
                "layout.edges",
                f"Edge {edge.edge_id} references unknown node(s): {', '.join(sorted(missing))}.",
                [edge.edge_id],
            )
        if edge.distance_m <= 0:
            out.add(
                "INVALID_EDGE_DISTANCE",
                "error",
                "layout.edges",
                f"Edge {edge.edge_id} has a non-positive distance_m ({edge.distance_m}).",
                [edge.edge_id],
            )
        if edge.from_node == edge.to_node:
            out.add(
                "SELF_LOOP_EDGE",
                "warning",
                "layout.edges",
                f"Edge {edge.edge_id} starts and ends at {edge.from_node}; it adds no travel.",
                [edge.edge_id],
            )
        if not edge.allowed_modes:
            out.add(
                "EDGE_WITHOUT_MODE",
                "error",
                "layout.edges",
                f"Edge {edge.edge_id} lists no allowed_modes and can never be traversed.",
                [edge.edge_id],
            )

    if dataset.nodes:
        components = graph.weakly_connected_components()
        if len(components) > 1:
            largest = max(components, key=len)
            isolated = sorted(n for c in components if c is not largest for n in c)
            out.add(
                "DISCONNECTED_GRAPH",
                "error",
                "layout.edges",
                f"The aisle graph has {len(components)} disconnected components. "
                f"Nodes outside the largest component: {', '.join(isolated)}.",
                isolated,
            )


def _check_config(dataset: Dataset, out: _Collector) -> None:
    node_ids = {n.node_id for n in dataset.nodes}
    for key in ("route_start_node", "route_end_node"):
        value = dataset.config.get(key)
        if not value:
            out.add(
                "MISSING_ROUTE_ENDPOINT",
                "error",
                f"config.{key}",
                f"config.{key} is not set; routes cannot be reconstructed.",
                [],
            )
        elif value not in node_ids:
            out.add(
                "UNKNOWN_NODE_REFERENCE",
                "error",
                f"config.{key}",
                f"config.{key} references unknown node {value!r}.",
                [str(value)],
            )
    mode = dataset.config.get("default_mode")
    if mode not in MODES:
        out.add(
            "UNKNOWN_MODE",
            "error",
            "config.default_mode",
            f"config.default_mode {mode!r} is not one of {', '.join(MODES)}.",
            [],
        )
    basis = dataset.config.get("abc_basis")
    if basis not in ("pick_lines", "units", "orders"):
        out.add(
            "UNKNOWN_ABC_BASIS",
            "error",
            "config.abc_basis",
            f"config.abc_basis {basis!r} is not supported.",
            [],
        )
    thresholds = dataset.config.get("abc_thresholds") or {}
    try:
        a_threshold = float(thresholds.get("A"))
        b_threshold = float(thresholds.get("B"))
    except (TypeError, ValueError):
        out.add(
            "INVALID_ABC_THRESHOLDS",
            "error",
            "config.abc_thresholds",
            "config.abc_thresholds must provide numeric A and B values.",
            [],
        )
    else:
        if not 0 < a_threshold < b_threshold < 1.0000001:
            out.add(
                "INVALID_ABC_THRESHOLDS",
                "error",
                "config.abc_thresholds",
                f"abc_thresholds must satisfy 0 < A < B <= 1, found A={a_threshold}, "
                f"B={b_threshold}.",
                [],
            )


def _check_locations(dataset: Dataset, out: _Collector) -> None:
    node_ids = {n.node_id for n in dataset.nodes}
    for location in dataset.locations:
        if not location.access_node_id:
            out.add(
                "LOCATION_WITHOUT_ACCESS_NODE",
                "error",
                "locations.access_node_id",
                f"Location {location.location_id} has no access node.",
                [location.location_id],
            )
        elif location.access_node_id not in node_ids:
            out.add(
                "UNKNOWN_NODE_REFERENCE",
                "error",
                "locations.access_node_id",
                f"Location {location.location_id} references unknown access node "
                f"{location.access_node_id}.",
                [location.location_id],
            )
        for attribute in ("capacity_volume_m3", "capacity_weight_kg", "max_unit_weight_kg"):
            value = getattr(location, attribute)
            if value is not None and value <= 0:
                out.add(
                    "INVALID_CAPACITY",
                    "error",
                    f"locations.{attribute}",
                    f"Location {location.location_id} has a non-positive {attribute} ({value}).",
                    [location.location_id],
                )
        if (
            location.capacity_weight_kg is not None
            and location.max_unit_weight_kg is not None
            and location.max_unit_weight_kg > location.capacity_weight_kg
        ):
            out.add(
                "INCONSISTENT_CAPACITY",
                "error",
                "locations.max_unit_weight_kg",
                f"Location {location.location_id} allows a single unit heavier "
                f"({location.max_unit_weight_kg} kg) than its total capacity "
                f"({location.capacity_weight_kg} kg).",
                [location.location_id],
            )
        if location.location_type not in LOCATION_TYPES:  # pragma: no cover - parser guards
            out.add(
                "UNKNOWN_LOCATION_TYPE",
                "error",
                "locations.location_type",
                f"Location {location.location_id} has unsupported type "
                f"{location.location_type!r}.",
                [location.location_id],
            )
        if not location.allowed_modes:
            out.add(
                "LOCATION_WITHOUT_MODE",
                "warning",
                "locations.allowed_modes",
                f"Location {location.location_id} lists no allowed_modes; slotting "
                "recommendations into it will report insufficient constraint data.",
                [location.location_id],
            )


def _check_items(dataset: Dataset, out: _Collector) -> None:
    for item in dataset.items:
        for attribute in ("unit_volume_m3", "unit_weight_kg"):
            value = getattr(item, attribute)
            if value is not None and value <= 0:
                out.add(
                    "INVALID_ITEM_MEASURE",
                    "error",
                    f"items.{attribute}",
                    f"Item {item.sku} has a non-positive {attribute} ({value}).",
                    [item.sku],
                )
        if not item.description:
            out.add(
                "MISSING_ITEM_DESCRIPTION",
                "info",
                "items.description",
                f"Item {item.sku} has no description; reports will show the SKU only.",
                [item.sku],
            )


def _check_assignments(dataset: Dataset, out: _Collector) -> None:
    location_ids = {l.location_id for l in dataset.locations}
    sku_ids = {i.sku for i in dataset.items}
    locations = dataset.locations_by_id()
    items = dataset.items_by_sku()

    pick_count: Dict[str, List[str]] = {}
    occupancy: Dict[str, List[str]] = {}
    for assignment in dataset.assignments:
        label = f"{assignment.sku}@{assignment.location_id}"
        if assignment.sku not in sku_ids:
            out.add(
                "UNKNOWN_SKU",
                "error",
                "assignments.sku",
                f"Assignment {label} references unknown SKU {assignment.sku}.",
                [label],
            )
        if assignment.location_id not in location_ids:
            out.add(
                "UNKNOWN_LOCATION",
                "error",
                "assignments.location_id",
                f"Assignment {label} references unknown location {assignment.location_id}.",
                [label],
            )
        if assignment.role == "pick":
            pick_count.setdefault(assignment.sku, []).append(assignment.location_id)
            occupancy.setdefault(assignment.location_id, []).append(assignment.sku)
        if assignment.max_units is not None and assignment.max_units <= 0:
            out.add(
                "INVALID_CAPACITY",
                "error",
                "assignments.max_units",
                f"Assignment {label} has a non-positive max_units ({assignment.max_units}).",
                [label],
            )
        if (
            assignment.current_units is not None
            and assignment.max_units is not None
            and assignment.current_units > assignment.max_units
        ):
            out.add(
                "INCONSISTENT_CAPACITY",
                "error",
                "assignments.current_units",
                f"Assignment {label} holds more units ({assignment.current_units}) than "
                f"max_units ({assignment.max_units}).",
                [label],
            )

        location = locations.get(assignment.location_id)
        item = items.get(assignment.sku)
        if location is None or item is None:
            continue
        if assignment.role == "pick" and location.location_type != "pick":
            out.add(
                "PICK_ROLE_ON_NON_PICK_LOCATION",
                "error",
                "assignments.role",
                f"Assignment {label} has role 'pick' but location "
                f"{location.location_id} is of type {location.location_type!r}.",
                [label],
            )
        if (
            item.unit_weight_kg is not None
            and location.max_unit_weight_kg is not None
            and item.unit_weight_kg > location.max_unit_weight_kg
        ):
            out.add(
                "ITEM_TOO_HEAVY_FOR_LOCATION",
                "error",
                "assignments",
                f"Item {item.sku} weighs {item.unit_weight_kg} kg per unit but location "
                f"{location.location_id} accepts at most {location.max_unit_weight_kg} kg.",
                [label],
            )
        if (
            item.temperature_zone
            and location.temperature_zone
            and item.temperature_zone != location.temperature_zone
        ):
            out.add(
                "TEMPERATURE_INCOMPATIBLE",
                "error",
                "assignments",
                f"Item {item.sku} requires temperature zone {item.temperature_zone!r} but "
                f"location {location.location_id} is {location.temperature_zone!r}.",
                [label],
            )
        if (
            item.hazard_class
            and location.hazard_classes_allowed
            and item.hazard_class not in location.hazard_classes_allowed
        ):
            out.add(
                "HAZARD_INCOMPATIBLE",
                "error",
                "assignments",
                f"Item {item.sku} has hazard class {item.hazard_class!r} which location "
                f"{location.location_id} does not allow.",
                [label],
            )
        if (
            item.handling_mode
            and location.allowed_modes
            and item.handling_mode not in location.allowed_modes
        ):
            out.add(
                "MODE_NOT_ALLOWED",
                "error",
                "assignments",
                f"Item {item.sku} is handled with {item.handling_mode!r} but location "
                f"{location.location_id} allows {', '.join(location.allowed_modes) or 'nothing'}.",
                [label],
            )

    multiple = sorted(sku for sku, locs in pick_count.items() if len(locs) > 1)
    if multiple:
        out.add(
            "MULTIPLE_PICK_ASSIGNMENTS",
            "error",
            "assignments.role",
            "Schema 1.0 allows exactly one active pick assignment per SKU. "
            f"Affected SKUs: {', '.join(multiple)}.",
            multiple,
        )
    shared = sorted(loc for loc, skus in occupancy.items() if len(skus) > 1)
    if shared:
        out.add(
            "SHARED_PICK_LOCATION",
            "error",
            "assignments.location_id",
            "A pick location holds more than one SKU, which schema 1.0 does not model. "
            f"Affected locations: {', '.join(shared)}.",
            shared,
        )


def _check_picks(dataset: Dataset, out: _Collector) -> None:
    location_ids = {l.location_id for l in dataset.locations}
    sku_ids = {i.sku for i in dataset.items}
    period_start = _parse_iso(dataset.meta.period_start) if dataset.meta.period_start else None
    period_end = _parse_iso(dataset.meta.period_end) if dataset.meta.period_end else None

    if not dataset.picks:
        out.add("NO_PICKS", "error", "picks", "Dataset contains no pick events.", [])

    sequences: Dict[str, Dict[int, List[str]]] = {}
    missing_sequence_orders: List[str] = []

    for pick in dataset.picks:
        if pick.sku not in sku_ids:
            out.add(
                "UNKNOWN_SKU",
                "error",
                "picks.sku",
                f"Pick {pick.pick_id} references unknown SKU {pick.sku}.",
                [pick.pick_id],
            )
        if pick.location_id not in location_ids:
            out.add(
                "UNKNOWN_LOCATION",
                "error",
                "picks.location_id",
                f"Pick {pick.pick_id} references unknown location {pick.location_id}.",
                [pick.pick_id],
            )
        if not pick.order_id:
            out.add(
                "MISSING_ORDER_ID",
                "error",
                "picks.order_id",
                f"Pick {pick.pick_id} has no order_id.",
                [pick.pick_id],
            )
        if pick.quantity <= 0:
            out.add(
                "INVALID_QUANTITY",
                "error",
                "picks.quantity",
                f"Pick {pick.pick_id} has a non-positive quantity ({pick.quantity}).",
                [pick.pick_id],
            )
        if pick.pick_sequence is None:
            missing_sequence_orders.append(pick.order_id)
        else:
            if pick.pick_sequence <= 0:
                out.add(
                    "INVALID_PICK_SEQUENCE",
                    "error",
                    "picks.pick_sequence",
                    f"Pick {pick.pick_id} has a non-positive pick_sequence "
                    f"({pick.pick_sequence}).",
                    [pick.pick_id],
                )
            sequences.setdefault(pick.order_id, {}).setdefault(pick.pick_sequence, []).append(
                pick.pick_id
            )
        if pick.mode and pick.mode not in MODES:  # pragma: no cover - parser guards
            out.add(
                "UNKNOWN_MODE",
                "error",
                "picks.mode",
                f"Pick {pick.pick_id} uses unsupported mode {pick.mode!r}.",
                [pick.pick_id],
            )
        if pick.timestamp:
            stamp = _parse_iso(pick.timestamp)
            if stamp is None:
                out.add(
                    "INVALID_TIMESTAMP",
                    "error",
                    "picks.timestamp",
                    f"Pick {pick.pick_id} has a non ISO 8601 timestamp {pick.timestamp!r}.",
                    [pick.pick_id],
                )
            elif period_start and period_end:
                if stamp.tzinfo is None or period_start.tzinfo is None:
                    comparable = stamp.replace(tzinfo=None)
                    lower = period_start.replace(tzinfo=None)
                    upper = period_end.replace(tzinfo=None)
                else:
                    comparable, lower, upper = stamp, period_start, period_end
                if comparable < lower or comparable > upper:
                    out.add(
                        "TIMESTAMP_OUTSIDE_PERIOD",
                        "error",
                        "picks.timestamp",
                        f"Pick {pick.pick_id} at {pick.timestamp} falls outside the declared "
                        f"period {dataset.meta.period_start} .. {dataset.meta.period_end}.",
                        [pick.pick_id],
                    )

    for order_id, by_sequence in sorted(sequences.items()):
        duplicated = sorted(seq for seq, ids in by_sequence.items() if len(ids) > 1)
        if duplicated:
            affected = sorted(pid for seq in duplicated for pid in by_sequence[seq])
            out.add(
                "DUPLICATE_PICK_SEQUENCE",
                "error",
                "picks.pick_sequence",
                f"Order {order_id} repeats pick_sequence value(s) "
                f"{', '.join(str(s) for s in duplicated)}.",
                affected,
            )

    for order_id in sorted(set(missing_sequence_orders)):
        out.add(
            "MISSING_PICK_SEQUENCE",
            "warning",
            "picks.pick_sequence",
            f"Order {order_id} has at least one pick without pick_sequence. Frequency and "
            "affinity stay valid; route reconstruction is unavailable for this order and no "
            "travel distance is estimated for it.",
            [order_id],
        )


def _check_order_modes(dataset: Dataset, out: _Collector) -> None:
    for order_id, picks in dataset.orders().items():
        _, mixed = dataset.order_mode(picks)
        if mixed:
            modes = sorted({p.mode for p in picks if p.mode})
            out.add(
                "MIXED_ORDER_MODES",
                "error",
                "picks.mode",
                f"Order {order_id} mixes travel modes ({', '.join(modes)}). Schema 1.0 routes "
                "one mode per order; the order is not routed.",
                [order_id],
            )


def _check_routes(dataset: Dataset, routing: RoutingResult, out: _Collector) -> None:
    for route in routing.routes:
        if route.complete:
            continue
        if route.reason == "UNREACHABLE_SEGMENT":
            for segment in route.unreachable_segments:
                out.add(
                    "DISCONNECTED_ROUTE",
                    "error",
                    "layout.edges",
                    f"No {segment.get('mode')} route exists between "
                    f"{segment.get('from_node')} and {segment.get('to_node')}.",
                    [segment.get("pick_id") or route.order_id],
                )
        elif route.reason in ("MISSING_PICK_SEQUENCE", "MIXED_ORDER_MODES"):
            continue  # already reported at record level
        elif route.reason:
            out.add(
                route.reason,
                "error",
                "picks",
                f"Order {route.order_id} could not be routed ({route.reason}).",
                [route.order_id],
            )


def validate(dataset: Dataset, graph: Optional[Graph] = None) -> ValidationReport:
    """Run every deterministic check and derive the analysis status."""
    out = _Collector()
    graph = graph or build_graph(dataset.nodes, dataset.edges)

    _check_meta(dataset, out)
    _check_duplicates(dataset, out)
    _check_layout(dataset, graph, out)
    _check_config(dataset, out)
    _check_locations(dataset, out)
    _check_items(dataset, out)
    _check_assignments(dataset, out)
    _check_picks(dataset, out)
    _check_order_modes(dataset, out)

    blocking_before_routing = [
        i for i in out.issues if i.severity == "error" and i.code in STRUCTURAL_CODES
    ]
    routing: Optional[RoutingResult] = None
    if not blocking_before_routing:
        routing = build_routes(dataset, graph)
        _check_routes(dataset, routing, out)

    total_orders = len(dataset.orders())
    routable = len(routing.complete_routes) if routing else 0
    unroutable = total_orders - routable

    status = _derive_status(out.issues, total_orders, routable)
    assert status in ANALYSIS_STATUSES
    return ValidationReport(
        status=status,
        issues=out.issues,
        routable_orders=routable,
        unroutable_orders=unroutable,
        total_orders=total_orders,
    )


def _derive_status(issues: Sequence[Issue], total_orders: int, routable: int) -> str:
    for issue in issues:
        if issue.severity == "error" and issue.code in STRUCTURAL_CODES:
            return "data_error"
    if total_orders == 0 or routable == 0:
        return "insufficient_evidence"
    if routable < total_orders:
        return "partial"
    if any(i.severity == "error" for i in issues):
        return "partial"
    return "ok"
