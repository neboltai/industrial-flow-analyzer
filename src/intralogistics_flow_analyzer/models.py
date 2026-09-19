"""Canonical data model for the Intralogistics Flow & Slotting Analyzer.

Every structure in this module maps one-to-one onto a block of the canonical
``dataset.json`` document described in ``schemas/dataset.schema.json``.

Design rules enforced here:

* No value is ever guessed. A field that is absent in the source document stays
  ``None`` and is reported by :mod:`intralogistics_flow_analyzer.validation`.
* Parsing is strict: an unknown enumeration member raises instead of being
  silently coerced to a default.
* Nothing in this module mutates a loaded dataset. Simulation works on copies.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "0.1.2"

#: Where the pick sequence of an order comes from. This decides what a
#: reconstructed route may be called. ``unknown`` is the backward-compatible
#: default for schema 1.0 documents written before the field existed.
SEQUENCE_BASES = ("planned", "scan_confirmed", "observed", "unknown")
DEFAULT_SEQUENCE_BASIS = "unknown"

#: How a distance is obtained. Schema 1.0 computes the shortest permitted path
#: on the declared aisle graph; it never measures movement. A future revision
#: adding, for example, telemetry-derived distances would extend this tuple.
DISTANCE_BASES = ("declared_graph_shortest_path",)
DEFAULT_DISTANCE_BASIS = "declared_graph_shortest_path"

#: Human-readable wording used in reports, so the two fields are explained the
#: same way everywhere.
SEQUENCE_BASIS_LABELS = {
    "planned": "planned by the system (the walked order may have differed)",
    "scan_confirmed": "confirmed by scan at each pick",
    "observed": "observed from movement records",
    "unknown": "not declared by the source system",
}

DISTANCE_BASIS_LABELS = {
    "declared_graph_shortest_path": (
        "shortest permitted path on the declared aisle graph, between consecutive "
        "pick access nodes, in the given sequence"
    ),
}

NODE_TYPES = (
    "junction",
    "depot",
    "receiving",
    "shipping",
    "packing",
    "location_access",
)

MODES = ("pedestrian", "forklift", "tugger")

LOCATION_TYPES = ("pick", "reserve", "buffer", "staging")

ASSIGNMENT_ROLES = ("pick", "reserve")

ANALYSIS_STATUSES = ("ok", "partial", "insufficient_evidence", "data_error")

CLAIM_TYPES = ("observed", "calculated", "simulated", "hypothesis")

EVIDENCE_QUALITIES = ("high", "medium", "low", "insufficient")

SEVERITIES = ("error", "warning", "info")

#: Columns that identify an individual worker. The normalizer drops them and the
#: engine never reads them. See ``skills/.../references/evidence-rules.md``.
PERSONAL_COLUMNS = (
    # English
    "employee_id",
    "employee_name",
    "worker_id",
    "worker_name",
    "picker_id",
    "picker_name",
    "operator_id",
    "operator_name",
    "user_id",
    "user_name",
    "staff_id",
    "staff_name",
    "badge_id",
    # German, as they appear in SAP EWM, LVS and Kommissionier exports
    "mitarbeiter_id",
    "mitarbeiter_name",
    "mitarbeiternummer",
    "personalnummer",
    "personal_nr",
    "kommissionierer",
    "kommissionierer_id",
    "kommissionierer_name",
    "bediener_id",
    "bediener_name",
    "benutzer_id",
    "benutzername",
    "lagerarbeiter",
    "ausweisnummer",
)

#: Substrings that make a column *suspected* of identifying a person without
#: matching an exact name. Such a column is never dropped silently: it is kept
#: out of the canonical dataset only when the mapping does not claim it, and the
#: normalisation report always names it.
PERSONAL_COLUMN_HINTS = (
    "employee",
    "worker",
    "picker",
    "operator",
    "mitarbeiter",
    "personalnummer",
    "kommissionierer",
    "bediener",
    "ausweis",
    "badge",
)

DEFAULT_CONFIG: Dict[str, Any] = {
    "route_start_node": None,
    "route_end_node": None,
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
    "restrict_swaps_to_same_zone": False,
    "allow_level_change": False,
    "spaghetti_top_orders": 10,
}

ABC_BASES = ("pick_lines", "units", "orders")

XYZ_BUCKETS = ("day", "week")


class DatasetError(ValueError):
    """Raised when a document cannot be parsed into the canonical model."""


def _require(mapping: Dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping or mapping[key] is None or mapping[key] == "":
        raise DatasetError(f"{context}: missing required field '{key}'")
    return mapping[key]


def _as_float(value: Any, context: str, key: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise DatasetError(f"{context}: field '{key}' is not numeric ({value!r})") from exc


def _opt_float(value: Any, context: str, key: str) -> Optional[float]:
    if value is None or value == "":
        return None
    return _as_float(value, context, key)


def _as_int(value: Any, context: str, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"{context}: field '{key}' is not an integer ({value!r})") from exc


def _opt_int(value: Any, context: str, key: str) -> Optional[int]:
    if value is None or value == "":
        return None
    return _as_int(value, context, key)


def _as_bool(value: Any, context: str, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "y"):
            return True
        if lowered in ("false", "0", "no", "n"):
            return False
    raise DatasetError(f"{context}: field '{key}' is not a boolean ({value!r})")


def _as_enum(value: Any, allowed: Iterable[str], context: str, key: str) -> str:
    text = str(value).strip()
    if text not in allowed:
        raise DatasetError(
            f"{context}: field '{key}' has unsupported value {value!r} "
            f"(allowed: {', '.join(allowed)})"
        )
    return text


def _as_str_list(value: Any, context: str, key: str) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split("|") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    raise DatasetError(f"{context}: field '{key}' is not a list ({value!r})")


@dataclass(frozen=True)
class Node:
    node_id: str
    x_m: float
    y_m: float
    node_type: str
    zone_id: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Node":
        context = f"layout.nodes[{raw.get('node_id', '?')}]"
        return cls(
            node_id=str(_require(raw, "node_id", context)),
            x_m=_as_float(_require(raw, "x_m", context), context, "x_m"),
            y_m=_as_float(_require(raw, "y_m", context), context, "y_m"),
            node_type=_as_enum(
                _require(raw, "node_type", context), NODE_TYPES, context, "node_type"
            ),
            zone_id=(str(raw["zone_id"]) if raw.get("zone_id") else None),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "node_type": self.node_type,
            "zone_id": self.zone_id,
        }


@dataclass(frozen=True)
class Edge:
    edge_id: str
    from_node: str
    to_node: str
    distance_m: float
    bidirectional: bool
    allowed_modes: Tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Edge":
        context = f"layout.edges[{raw.get('edge_id', '?')}]"
        modes = _as_str_list(_require(raw, "allowed_modes", context), context, "allowed_modes")
        for mode in modes:
            _as_enum(mode, MODES, context, "allowed_modes")
        return cls(
            edge_id=str(_require(raw, "edge_id", context)),
            from_node=str(_require(raw, "from_node", context)),
            to_node=str(_require(raw, "to_node", context)),
            distance_m=_as_float(_require(raw, "distance_m", context), context, "distance_m"),
            bidirectional=_as_bool(
                _require(raw, "bidirectional", context), context, "bidirectional"
            ),
            allowed_modes=tuple(modes),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "distance_m": self.distance_m,
            "bidirectional": self.bidirectional,
            "allowed_modes": list(self.allowed_modes),
        }


@dataclass(frozen=True)
class Location:
    location_id: str
    access_node_id: str
    zone_id: Optional[str]
    location_type: str
    capacity_volume_m3: Optional[float]
    capacity_weight_kg: Optional[float]
    max_unit_weight_kg: Optional[float]
    temperature_zone: Optional[str]
    hazard_classes_allowed: Tuple[str, ...]
    allowed_modes: Tuple[str, ...]
    level: Optional[int]
    fixed: bool

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Location":
        context = f"locations[{raw.get('location_id', '?')}]"
        modes = _as_str_list(raw.get("allowed_modes"), context, "allowed_modes")
        for mode in modes:
            _as_enum(mode, MODES, context, "allowed_modes")
        return cls(
            location_id=str(_require(raw, "location_id", context)),
            access_node_id=str(_require(raw, "access_node_id", context)),
            zone_id=(str(raw["zone_id"]) if raw.get("zone_id") else None),
            location_type=_as_enum(
                _require(raw, "location_type", context),
                LOCATION_TYPES,
                context,
                "location_type",
            ),
            capacity_volume_m3=_opt_float(
                raw.get("capacity_volume_m3"), context, "capacity_volume_m3"
            ),
            capacity_weight_kg=_opt_float(
                raw.get("capacity_weight_kg"), context, "capacity_weight_kg"
            ),
            max_unit_weight_kg=_opt_float(
                raw.get("max_unit_weight_kg"), context, "max_unit_weight_kg"
            ),
            temperature_zone=(
                str(raw["temperature_zone"]) if raw.get("temperature_zone") else None
            ),
            hazard_classes_allowed=tuple(
                _as_str_list(raw.get("hazard_classes_allowed"), context, "hazard_classes_allowed")
            ),
            allowed_modes=tuple(modes),
            level=_opt_int(raw.get("level"), context, "level"),
            fixed=_as_bool(raw.get("fixed", False), context, "fixed"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "location_id": self.location_id,
            "access_node_id": self.access_node_id,
            "zone_id": self.zone_id,
            "location_type": self.location_type,
            "capacity_volume_m3": self.capacity_volume_m3,
            "capacity_weight_kg": self.capacity_weight_kg,
            "max_unit_weight_kg": self.max_unit_weight_kg,
            "temperature_zone": self.temperature_zone,
            "hazard_classes_allowed": list(self.hazard_classes_allowed),
            "allowed_modes": list(self.allowed_modes),
            "level": self.level,
            "fixed": self.fixed,
        }


@dataclass(frozen=True)
class Item:
    sku: str
    description: str
    unit_volume_m3: Optional[float]
    unit_weight_kg: Optional[float]
    temperature_zone: Optional[str]
    hazard_class: Optional[str]
    handling_mode: Optional[str]
    fixed_location: bool

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Item":
        context = f"items[{raw.get('sku', '?')}]"
        handling_mode = raw.get("handling_mode")
        if handling_mode:
            handling_mode = _as_enum(handling_mode, MODES, context, "handling_mode")
        else:
            handling_mode = None
        return cls(
            sku=str(_require(raw, "sku", context)),
            description=str(raw.get("description") or ""),
            unit_volume_m3=_opt_float(raw.get("unit_volume_m3"), context, "unit_volume_m3"),
            unit_weight_kg=_opt_float(raw.get("unit_weight_kg"), context, "unit_weight_kg"),
            temperature_zone=(
                str(raw["temperature_zone"]) if raw.get("temperature_zone") else None
            ),
            hazard_class=(str(raw["hazard_class"]) if raw.get("hazard_class") else None),
            handling_mode=handling_mode,
            fixed_location=_as_bool(raw.get("fixed_location", False), context, "fixed_location"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sku": self.sku,
            "description": self.description,
            "unit_volume_m3": self.unit_volume_m3,
            "unit_weight_kg": self.unit_weight_kg,
            "temperature_zone": self.temperature_zone,
            "hazard_class": self.hazard_class,
            "handling_mode": self.handling_mode,
            "fixed_location": self.fixed_location,
        }


@dataclass(frozen=True)
class Assignment:
    sku: str
    location_id: str
    role: str
    current_units: Optional[int]
    max_units: Optional[int]

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Assignment":
        context = f"assignments[{raw.get('sku', '?')}@{raw.get('location_id', '?')}]"
        return cls(
            sku=str(_require(raw, "sku", context)),
            location_id=str(_require(raw, "location_id", context)),
            role=_as_enum(_require(raw, "role", context), ASSIGNMENT_ROLES, context, "role"),
            current_units=_opt_int(raw.get("current_units"), context, "current_units"),
            max_units=_opt_int(raw.get("max_units"), context, "max_units"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sku": self.sku,
            "location_id": self.location_id,
            "role": self.role,
            "current_units": self.current_units,
            "max_units": self.max_units,
        }


@dataclass(frozen=True)
class Pick:
    pick_id: str
    order_id: str
    pick_sequence: Optional[int]
    timestamp: Optional[str]
    sku: str
    location_id: str
    quantity: float
    mode: Optional[str]

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Pick":
        context = f"picks[{raw.get('pick_id', '?')}]"
        mode = raw.get("mode")
        if mode:
            mode = _as_enum(mode, MODES, context, "mode")
        else:
            mode = None
        return cls(
            pick_id=str(_require(raw, "pick_id", context)),
            order_id=str(_require(raw, "order_id", context)),
            pick_sequence=_opt_int(raw.get("pick_sequence"), context, "pick_sequence"),
            timestamp=(str(raw["timestamp"]) if raw.get("timestamp") else None),
            sku=str(_require(raw, "sku", context)),
            location_id=str(_require(raw, "location_id", context)),
            quantity=_as_float(_require(raw, "quantity", context), context, "quantity"),
            mode=mode,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pick_id": self.pick_id,
            "order_id": self.order_id,
            "pick_sequence": self.pick_sequence,
            "timestamp": self.timestamp,
            "sku": self.sku,
            "location_id": self.location_id,
            "quantity": self.quantity,
            "mode": self.mode,
        }


@dataclass
class Meta:
    dataset_id: str
    site_id: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    distance_unit: str = "m"
    time_zone: Optional[str] = None
    source: Optional[str] = None
    fictional: bool = True
    #: Additive in 0.1.1. A document written against schema 1.0 before these
    #: fields existed loads unchanged and receives the conservative defaults.
    sequence_basis: str = DEFAULT_SEQUENCE_BASIS
    distance_basis: str = DEFAULT_DISTANCE_BASIS

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Meta":
        context = "meta"
        sequence_basis = raw.get("sequence_basis") or DEFAULT_SEQUENCE_BASIS
        distance_basis = raw.get("distance_basis") or DEFAULT_DISTANCE_BASIS
        return cls(
            dataset_id=str(_require(raw, "dataset_id", context)),
            site_id=(str(raw["site_id"]) if raw.get("site_id") else None),
            period_start=(str(raw["period_start"]) if raw.get("period_start") else None),
            period_end=(str(raw["period_end"]) if raw.get("period_end") else None),
            distance_unit=str(raw.get("distance_unit") or "m"),
            time_zone=(str(raw["time_zone"]) if raw.get("time_zone") else None),
            source=(str(raw["source"]) if raw.get("source") else None),
            fictional=_as_bool(raw.get("fictional", True), context, "fictional"),
            # Parsing stays permissive so that an unknown value reaches validation
            # as a reported issue instead of failing the load with a stack trace.
            sequence_basis=str(sequence_basis),
            distance_basis=str(distance_basis),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "site_id": self.site_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "distance_unit": self.distance_unit,
            "time_zone": self.time_zone,
            "source": self.source,
            "fictional": self.fictional,
            "sequence_basis": self.sequence_basis,
            "distance_basis": self.distance_basis,
        }

    def sequence_basis_label(self) -> str:
        return SEQUENCE_BASIS_LABELS.get(self.sequence_basis, "declared with an unsupported value")

    def distance_basis_label(self) -> str:
        return DISTANCE_BASIS_LABELS.get(self.distance_basis, "declared with an unsupported value")

    def sequence_is_confirmed(self) -> bool:
        """True only when the sequence is evidence of what happened.

        ``planned`` and ``unknown`` describe an intention or nothing at all, so
        they never support a claim about what was actually walked.
        """
        return self.sequence_basis in ("scan_confirmed", "observed")


@dataclass
class Issue:
    """A single, explicit finding produced by validation or by a loader."""

    code: str
    severity: str
    field: str
    message: str
    affected_records: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "field": self.field,
            "message": self.message,
            "affected_records": list(self.affected_records),
        }


@dataclass
class Dataset:
    schema_version: str
    meta: Meta
    nodes: List[Node]
    edges: List[Edge]
    locations: List[Location]
    items: List[Item]
    assignments: List[Assignment]
    picks: List[Pick]
    config: Dict[str, Any]

    # ---------------------------------------------------------------- parsing

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Dataset":
        if not isinstance(raw, dict):
            raise DatasetError("dataset document must be a JSON object")
        layout = raw.get("layout") or {}
        if not isinstance(layout, dict):
            raise DatasetError("layout must be a JSON object")
        config = dict(DEFAULT_CONFIG)
        supplied_config = raw.get("config") or {}
        if not isinstance(supplied_config, dict):
            raise DatasetError("config must be a JSON object")
        config.update(supplied_config)
        return cls(
            schema_version=str(raw.get("schema_version") or SCHEMA_VERSION),
            meta=Meta.from_dict(raw.get("meta") or {}),
            nodes=[Node.from_dict(n) for n in layout.get("nodes") or []],
            edges=[Edge.from_dict(e) for e in layout.get("edges") or []],
            locations=[Location.from_dict(l) for l in raw.get("locations") or []],
            items=[Item.from_dict(i) for i in raw.get("items") or []],
            assignments=[Assignment.from_dict(a) for a in raw.get("assignments") or []],
            picks=[Pick.from_dict(p) for p in raw.get("picks") or []],
            config=config,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "meta": self.meta.to_dict(),
            "layout": {
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [e.to_dict() for e in self.edges],
            },
            "locations": [l.to_dict() for l in self.locations],
            "items": [i.to_dict() for i in self.items],
            "assignments": [a.to_dict() for a in self.assignments],
            "picks": [p.to_dict() for p in self.picks],
            "config": dict(self.config),
        }

    def copy(self) -> "Dataset":
        """Return a deep copy. Simulation never mutates the source dataset."""
        return Dataset.from_dict(copy.deepcopy(self.to_dict()))

    # ------------------------------------------------------------- accessors

    def nodes_by_id(self) -> Dict[str, Node]:
        return {n.node_id: n for n in self.nodes}

    def locations_by_id(self) -> Dict[str, Location]:
        return {l.location_id: l for l in self.locations}

    def items_by_sku(self) -> Dict[str, Item]:
        return {i.sku: i for i in self.items}

    def pick_assignments(self) -> Dict[str, Assignment]:
        """Active ``pick`` assignment per SKU (v0.1 allows exactly one)."""
        result: Dict[str, Assignment] = {}
        for assignment in self.assignments:
            if assignment.role == "pick" and assignment.sku not in result:
                result[assignment.sku] = assignment
        return result

    def pick_location_of(self) -> Dict[str, str]:
        return {sku: a.location_id for sku, a in self.pick_assignments().items()}

    def sku_at_pick_location(self) -> Dict[str, str]:
        return {a.location_id: sku for sku, a in self.pick_assignments().items()}

    def orders(self) -> Dict[str, List[Pick]]:
        """Group picks by ``order_id``.

        Picks are ordered by ``pick_sequence``. The file order is never used as
        a silent substitute: an order with any missing sequence is returned in
        the order encountered and flagged by validation as unroutable.
        """
        grouped: Dict[str, List[Pick]] = {}
        for pick in self.picks:
            grouped.setdefault(pick.order_id, []).append(pick)
        ordered: Dict[str, List[Pick]] = {}
        for order_id in sorted(grouped):
            picks = grouped[order_id]
            if all(p.pick_sequence is not None for p in picks):
                picks = sorted(picks, key=lambda p: (p.pick_sequence, p.pick_id))
            ordered[order_id] = picks
        return ordered

    def order_has_sequence(self, picks: List[Pick]) -> bool:
        return all(p.pick_sequence is not None for p in picks)

    def order_mode(self, picks: List[Pick]) -> Tuple[str, bool]:
        """Return ``(mode, mixed)`` for an order.

        ``mixed`` is ``True`` when picks disagree; the configured default mode is
        then returned and validation raises ``MIXED_ORDER_MODES``.
        """
        modes = {p.mode for p in picks if p.mode}
        default_mode = str(self.config.get("default_mode") or "pedestrian")
        if len(modes) == 1:
            return modes.pop(), False
        if not modes:
            return default_mode, False
        return default_mode, True
