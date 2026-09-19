"""CSV to canonical ``dataset.json`` normalisation driven by an explicit mapping.

The normalizer performs *syntactic* transformations only. It never infers:

* a unit,
* the meaning of a column,
* the direction of an aisle,
* a temperature zone,
* a hazard class,
* the capacity of a location.

Anything not stated in ``mapping.json`` raises :class:`NormalizationError`, which
carries the open question the mapping has to answer. An assistant may *propose* a
mapping; the engine only executes one a human has written down.

Worker-identifying columns (``employee_id``, ``picker_name``, ...) are dropped
before any parsing, reported as unnecessary, and never appear in the output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .loaders import csv_headers, read_csv_rows, read_json
from .models import (
    DEFAULT_DISTANCE_BASIS,
    DEFAULT_SEQUENCE_BASIS,
    DISTANCE_BASES,
    Issue,
    PERSONAL_COLUMNS,
    PERSONAL_COLUMN_HINTS,
    SCHEMA_VERSION,
    SEQUENCE_BASES,
)

#: Only these purely syntactic transformations are allowed in a mapping.
TRANSFORMS: Dict[str, Callable[[str], str]] = {
    "strip": lambda value: value.strip(),
    "upper": lambda value: value.strip().upper(),
    "lower": lambda value: value.strip().lower(),
    "decimal_comma_to_point": lambda value: value.strip().replace(",", "."),
    "remove_thousands_separator": lambda value: value.strip().replace(" ", "").replace("'", ""),
    "pipe_from_semicolon": lambda value: "|".join(
        part.strip() for part in value.split(";") if part.strip()
    ),
    "pipe_from_comma": lambda value: "|".join(
        part.strip() for part in value.split(",") if part.strip()
    ),
}

#: A mapping must declare a unit for every physical quantity, and the unit must
#: be one the engine can convert without guessing.
UNIT_FACTORS: Dict[str, Dict[str, float]] = {
    "distance": {"m": 1.0, "cm": 0.01, "mm": 0.001, "km": 1000.0},
    "volume": {"m3": 1.0, "dm3": 0.001, "l": 0.001, "cm3": 0.000001},
    "weight": {"kg": 1.0, "g": 0.001, "t": 1000.0},
}

#: Required target fields per canonical block.
REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "nodes": ("node_id", "x_m", "y_m", "node_type"),
    "edges": ("edge_id", "from_node", "to_node", "distance_m", "bidirectional", "allowed_modes"),
    "locations": ("location_id", "access_node_id", "location_type"),
    "items": ("sku",),
    "assignments": ("sku", "location_id", "role"),
    "picks": ("pick_id", "order_id", "sku", "location_id", "quantity"),
}

QUANTITY_UNITS: Dict[Tuple[str, str], str] = {
    ("edges", "distance_m"): "distance",
    ("nodes", "x_m"): "distance",
    ("nodes", "y_m"): "distance",
    ("locations", "capacity_volume_m3"): "volume",
    ("locations", "capacity_weight_kg"): "weight",
    ("locations", "max_unit_weight_kg"): "weight",
    ("items", "unit_volume_m3"): "volume",
    ("items", "unit_weight_kg"): "weight",
}


class NormalizationError(ValueError):
    """Raised when the mapping does not answer a question the engine must not guess."""


class NormalizationQuestion(NormalizationError):
    """A normalisation question addressed to the person who owns the export."""


@dataclass
class NormalizationResult:
    dataset: Dict[str, Any]
    issues: List[Issue] = field(default_factory=list)
    dropped_columns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset,
            "issues": [i.to_dict() for i in self.issues],
            "dropped_personal_columns": list(self.dropped_columns),
        }


def _apply_transforms(value: str, transforms: Sequence[str], context: str) -> str:
    result = value
    for name in transforms:
        function = TRANSFORMS.get(name)
        if function is None:
            raise NormalizationError(
                f"{context}: unknown transform {name!r}. Allowed transforms are purely "
                f"syntactic: {', '.join(sorted(TRANSFORMS))}."
            )
        result = function(result)
    return result


def _convert_unit(value: str, quantity: str, unit: str, context: str) -> str:
    factors = UNIT_FACTORS[quantity]
    factor = factors.get(unit)
    if factor is None:
        raise NormalizationQuestion(
            f"{context}: unit {unit!r} is not a known {quantity} unit. Declare one of "
            f"{', '.join(sorted(factors))} in the mapping, or convert the export first."
        )
    if value == "":
        return ""
    try:
        # Rounded so that a unit conversion is byte-for-byte reproducible.
        return repr(round(float(value) * factor, 9))
    except ValueError as exc:
        raise NormalizationError(f"{context}: value {value!r} is not numeric.") from exc


def _block_spec(mapping: Dict[str, Any], block: str) -> Dict[str, Any]:
    blocks = mapping.get("blocks")
    if not isinstance(blocks, dict):
        raise NormalizationError("mapping.json must contain a 'blocks' object.")
    spec = blocks.get(block)
    if not isinstance(spec, dict):
        raise NormalizationQuestion(
            f"mapping.json does not describe the '{block}' block. Declare its file name and "
            "column mapping, or state explicitly that the block is absent."
        )
    return spec


def _normalise_block(
    block: str,
    spec: Dict[str, Any],
    input_dir: Path,
    issues: List[Issue],
    dropped: List[str],
) -> List[Dict[str, Any]]:
    file_name = spec.get("file")
    if not file_name:
        raise NormalizationQuestion(
            f"mapping.blocks.{block}.file is not set. Name the CSV export for this block."
        )
    path = input_dir / str(file_name)
    columns = spec.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise NormalizationQuestion(
            f"mapping.blocks.{block}.columns is empty. Map every canonical field explicitly."
        )
    defaults = spec.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise NormalizationError(f"mapping.blocks.{block}.defaults must be an object.")

    headers = csv_headers(path)
    present = set(headers)
    personal_here = [h for h in headers if h.strip().lower() in PERSONAL_COLUMNS]
    mapped_sources = {
        str(rule.get("source")).strip().lower()
        for rule in (columns or {}).values()
        if isinstance(rule, dict) and rule.get("source")
    }
    # A column that merely looks like it identifies a person is never removed in
    # silence and never removed on a guess: it is reported, and it is only left
    # out of the canonical dataset because the mapping does not claim it.
    suspected_here = [
        h
        for h in headers
        if h.strip().lower() not in PERSONAL_COLUMNS
        and h.strip().lower() not in mapped_sources
        and any(hint in h.strip().lower() for hint in PERSONAL_COLUMN_HINTS)
    ]
    if suspected_here:
        issues.append(
            Issue(
                code="SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED",
                severity="warning",
                field=f"{block}",
                message=(
                    f"{path.name} contains column(s) {', '.join(sorted(suspected_here))} whose "
                    "name suggests they identify a person. They are not mapped, so they do not "
                    "reach the canonical dataset. Confirm the intent, and remove them at the "
                    "source if they are worker identifiers."
                ),
                affected_records=sorted(suspected_here),
            )
        )
    if personal_here:
        for column in personal_here:
            if column not in dropped:
                dropped.append(column)
        issues.append(
            Issue(
                code="PERSONAL_COLUMN_DROPPED",
                severity="info",
                field=f"{block}",
                message=(
                    f"{path.name} contains worker-identifying column(s) "
                    f"{', '.join(sorted(personal_here))}. They are not required for flow analysis "
                    "and were dropped; they never reach the canonical dataset or any report."
                ),
                affected_records=sorted(personal_here),
            )
        )

    for target, rule in columns.items():
        source = rule.get("source") if isinstance(rule, dict) else rule
        if isinstance(rule, dict) and rule.get("constant") is not None:
            continue
        if source is None:
            raise NormalizationQuestion(
                f"mapping.blocks.{block}.columns.{target} declares neither 'source' nor "
                "'constant'. State which CSV column carries this field."
            )
        if str(source).strip().lower() in PERSONAL_COLUMNS:
            raise NormalizationError(
                f"mapping.blocks.{block}.columns.{target} maps the worker-identifying column "
                f"{source!r}. The analyser does not process individual worker data."
            )
        if source not in present:
            raise NormalizationQuestion(
                f"mapping.blocks.{block}.columns.{target} refers to column {source!r} which is "
                f"absent from {path.name}. Available columns: {', '.join(headers)}."
            )

    missing_required = [
        field_name
        for field_name in REQUIRED_FIELDS.get(block, ())
        if field_name not in columns and field_name not in defaults
    ]
    if missing_required:
        raise NormalizationQuestion(
            f"mapping.blocks.{block} does not provide required field(s) "
            f"{', '.join(missing_required)}. Map a column or declare an explicit default."
        )

    rows_out: List[Dict[str, Any]] = []
    for index, raw in enumerate(read_csv_rows(path), start=2):
        record: Dict[str, Any] = {}
        for target, rule in columns.items():
            context = f"{path.name}:{index} -> {block}.{target}"
            if isinstance(rule, dict):
                if rule.get("constant") is not None:
                    record[target] = rule["constant"]
                    continue
                value = raw.get(str(rule.get("source")), "")
                value = _apply_transforms(value, rule.get("transforms") or [], context)
                quantity = QUANTITY_UNITS.get((block, target))
                if quantity:
                    unit = rule.get("unit")
                    if not unit:
                        raise NormalizationQuestion(
                            f"mapping.blocks.{block}.columns.{target} carries a {quantity} and "
                            "must declare its 'unit'. The engine does not guess units."
                        )
                    value = _convert_unit(value, quantity, str(unit), context)
                if value == "" and target in defaults:
                    value = defaults[target]
                record[target] = value
            else:
                record[target] = _apply_transforms(raw.get(str(rule), ""), [], context)
        for target, default in defaults.items():
            if target not in record or record[target] in ("", None):
                record[target] = default
        rows_out.append(record)
    return rows_out


def normalize(
    input_dir: Path,
    mapping: Dict[str, Any],
) -> NormalizationResult:
    """Turn a directory of CSV exports into a canonical dataset document."""
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise NormalizationError(f"Input directory not found: {input_dir}")

    issues: List[Issue] = []
    dropped: List[str] = []

    meta = mapping.get("meta")
    if not isinstance(meta, dict) or not meta.get("dataset_id"):
        raise NormalizationQuestion(
            "mapping.meta must declare at least 'dataset_id'. Period, site and time zone are "
            "part of the data contract and are not inferred from the files."
        )
    if meta.get("distance_unit", "m") != "m":
        raise NormalizationQuestion(
            "Schema 1.0 stores distances in metres. Declare meta.distance_unit as 'm' and "
            "convert the export, or declare per-column units in the mapping."
        )

    # Provenance of the pick sequence and of the distances. Both are additive in
    # 0.1.1: a mapping written for 0.1.0 omits them and gets the conservative
    # defaults, with the omission reported rather than assumed away.
    sequence_basis = str(meta.get("sequence_basis") or DEFAULT_SEQUENCE_BASIS)
    if sequence_basis not in SEQUENCE_BASES:
        raise NormalizationQuestion(
            f"mapping.meta.sequence_basis {sequence_basis!r} is not one of "
            f"{', '.join(SEQUENCE_BASES)}. State how the pick sequence was produced; the engine "
            "does not infer it."
        )
    if not meta.get("sequence_basis"):
        issues.append(
            Issue(
                code="SEQUENCE_BASIS_DEFAULTED",
                severity="info",
                field="meta.sequence_basis",
                message=(
                    "mapping.meta.sequence_basis is not declared; the dataset records 'unknown'. "
                    "Route distances are then modelled from a sequence of unstated provenance."
                ),
                affected_records=[str(meta["dataset_id"])],
            )
        )
    distance_basis = str(meta.get("distance_basis") or DEFAULT_DISTANCE_BASIS)
    if distance_basis not in DISTANCE_BASES:
        raise NormalizationQuestion(
            f"mapping.meta.distance_basis {distance_basis!r} is not supported in schema 1.0 "
            f"(supported: {', '.join(DISTANCE_BASES)})."
        )

    blocks: Dict[str, List[Dict[str, Any]]] = {}
    for block in ("nodes", "edges", "locations", "items", "assignments", "picks"):
        spec = _block_spec(mapping, block)
        blocks[block] = _normalise_block(block, spec, input_dir, issues, dropped)

    config = mapping.get("config")
    if not isinstance(config, dict):
        raise NormalizationQuestion(
            "mapping.config must be present and must declare route_start_node and "
            "route_end_node. The engine does not guess where a picking tour starts."
        )
    for key in ("route_start_node", "route_end_node"):
        if not config.get(key):
            raise NormalizationQuestion(
                f"mapping.config.{key} is not set. Name the node where a picking tour "
                f"{'starts' if key.endswith('start_node') else 'ends'}."
            )

    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "dataset_id": meta["dataset_id"],
            "site_id": meta.get("site_id"),
            "period_start": meta.get("period_start"),
            "period_end": meta.get("period_end"),
            "distance_unit": "m",
            "time_zone": meta.get("time_zone"),
            "source": meta.get("source"),
            "fictional": bool(meta.get("fictional", True)),
            "sequence_basis": sequence_basis,
            "distance_basis": distance_basis,
        },
        "layout": {
            "nodes": [_coerce_node(r) for r in blocks["nodes"]],
            "edges": [_coerce_edge(r) for r in blocks["edges"]],
        },
        "locations": [_coerce_location(r) for r in blocks["locations"]],
        "items": [_coerce_item(r) for r in blocks["items"]],
        "assignments": [_coerce_assignment(r) for r in blocks["assignments"]],
        "picks": [_coerce_pick(r) for r in blocks["picks"]],
        "config": dict(config),
    }

    issues.append(
        Issue(
            code="NORMALIZATION_COMPLETED",
            severity="info",
            field="meta",
            message=(
                f"Normalised {len(document['layout']['nodes'])} nodes, "
                f"{len(document['layout']['edges'])} edges, {len(document['locations'])} locations, "
                f"{len(document['items'])} items, {len(document['assignments'])} assignments and "
                f"{len(document['picks'])} pick events using the supplied mapping."
            ),
            affected_records=[str(meta["dataset_id"])],
        )
    )
    return NormalizationResult(dataset=document, issues=issues, dropped_columns=dropped)


def _number(value: Any) -> Optional[float]:
    if value in ("", None):
        return None
    return float(value)


def _integer(value: Any) -> Optional[int]:
    if value in ("", None):
        return None
    return int(float(value))


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if value in ("", None):
        return []
    return [part.strip() for part in str(value).split("|") if part.strip()]


def _coerce_node(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "node_id": row.get("node_id"),
        "x_m": _number(row.get("x_m")),
        "y_m": _number(row.get("y_m")),
        "node_type": row.get("node_type"),
        "zone_id": row.get("zone_id") or None,
    }


def _coerce_edge(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "edge_id": row.get("edge_id"),
        "from_node": row.get("from_node"),
        "to_node": row.get("to_node"),
        "distance_m": _number(row.get("distance_m")),
        "bidirectional": _boolean(row.get("bidirectional")),
        "allowed_modes": _list(row.get("allowed_modes")),
    }


def _coerce_location(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "location_id": row.get("location_id"),
        "access_node_id": row.get("access_node_id"),
        "zone_id": row.get("zone_id") or None,
        "location_type": row.get("location_type"),
        "capacity_volume_m3": _number(row.get("capacity_volume_m3")),
        "capacity_weight_kg": _number(row.get("capacity_weight_kg")),
        "max_unit_weight_kg": _number(row.get("max_unit_weight_kg")),
        "temperature_zone": row.get("temperature_zone") or None,
        "hazard_classes_allowed": _list(row.get("hazard_classes_allowed")),
        "allowed_modes": _list(row.get("allowed_modes")),
        "level": _integer(row.get("level")),
        "fixed": _boolean(row.get("fixed")),
    }


def _coerce_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sku": row.get("sku"),
        "description": row.get("description") or "",
        "unit_volume_m3": _number(row.get("unit_volume_m3")),
        "unit_weight_kg": _number(row.get("unit_weight_kg")),
        "temperature_zone": row.get("temperature_zone") or None,
        "hazard_class": row.get("hazard_class") or None,
        "handling_mode": row.get("handling_mode") or None,
        "fixed_location": _boolean(row.get("fixed_location")),
    }


def _coerce_assignment(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sku": row.get("sku"),
        "location_id": row.get("location_id"),
        "role": row.get("role"),
        "current_units": _integer(row.get("current_units")),
        "max_units": _integer(row.get("max_units")),
    }


def _coerce_pick(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pick_id": row.get("pick_id"),
        "order_id": row.get("order_id"),
        "pick_sequence": _integer(row.get("pick_sequence")),
        "timestamp": row.get("timestamp") or None,
        "sku": row.get("sku"),
        "location_id": row.get("location_id"),
        "quantity": _number(row.get("quantity")),
        "mode": row.get("mode") or None,
    }


def load_mapping(path: Path) -> Dict[str, Any]:
    document = read_json(Path(path))
    if not isinstance(document, dict):
        raise NormalizationError(f"{path} must contain a JSON object.")
    return document
