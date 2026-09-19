"""Constraint checks that gate every slotting recommendation.

Nine checks are evaluated for a candidate pair swap, in both directions:

``volume``        item volume x stored units against the target volume capacity
``weight``        item weight x stored units against the target weight capacity
``unit_weight``   single-unit weight against the target's max unit weight
``temperature``   item temperature zone against the target temperature zone
``hazard``        item hazard class against the target's allowed hazard classes
``handling_mode`` item handling mode against the target's allowed modes
``zone``          both locations are pick locations; optional same-zone rule
``level``         rack level change, allowed only when explicitly configured
``fixed``         neither location nor item may be pinned

Each check yields ``pass``, ``fail`` or ``insufficient_data``. A missing input is
never treated as a pass: an unknown constraint blocks the recommendation and the
candidate is reported with status ``insufficient_constraint_data``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import Assignment, Dataset, Item, Location

PASS = "pass"
FAIL = "fail"
INSUFFICIENT = "insufficient_data"

CHECK_ORDER = (
    "volume",
    "weight",
    "unit_weight",
    "temperature",
    "hazard",
    "handling_mode",
    "zone",
    "level",
    "fixed",
)

STATUS_OK = "ok"
STATUS_BLOCKED = "constraint_violation"
STATUS_INSUFFICIENT = "insufficient_constraint_data"


@dataclass
class ConstraintResult:
    checks: Dict[str, str] = field(default_factory=dict)
    reasons: Dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if any(value == FAIL for value in self.checks.values()):
            return STATUS_BLOCKED
        if any(value == INSUFFICIENT for value in self.checks.values()):
            return STATUS_INSUFFICIENT
        return STATUS_OK

    @property
    def allowed(self) -> bool:
        return self.status == STATUS_OK

    def failed_checks(self) -> List[str]:
        return [name for name in CHECK_ORDER if self.checks.get(name) == FAIL]

    def insufficient_checks(self) -> List[str]:
        return [name for name in CHECK_ORDER if self.checks.get(name) == INSUFFICIENT]

    def to_dict(self) -> Dict[str, Any]:
        return {name: self.checks.get(name, INSUFFICIENT) for name in CHECK_ORDER}


def _worst(*values: str) -> str:
    if FAIL in values:
        return FAIL
    if INSUFFICIENT in values:
        return INSUFFICIENT
    return PASS


def _units_for(assignment: Optional[Assignment]) -> Optional[int]:
    if assignment is None:
        return None
    if assignment.max_units is not None:
        return assignment.max_units
    return assignment.current_units


def _check_volume(item: Item, target: Location, units: Optional[int]) -> (str, str):
    if item.unit_volume_m3 is None or target.capacity_volume_m3 is None or units is None:
        return INSUFFICIENT, (
            f"Volume cannot be verified for {item.sku} in {target.location_id}: "
            "unit volume, stored units or location capacity is unknown."
        )
    required = item.unit_volume_m3 * units
    if required > target.capacity_volume_m3 + 1e-9:
        return FAIL, (
            f"{item.sku} needs {required:.4f} m3 in {target.location_id} which offers "
            f"{target.capacity_volume_m3:.4f} m3."
        )
    return PASS, ""


def _check_weight(item: Item, target: Location, units: Optional[int]) -> (str, str):
    if item.unit_weight_kg is None or target.capacity_weight_kg is None or units is None:
        return INSUFFICIENT, (
            f"Total weight cannot be verified for {item.sku} in {target.location_id}."
        )
    required = item.unit_weight_kg * units
    if required > target.capacity_weight_kg + 1e-9:
        return FAIL, (
            f"{item.sku} would load {required:.2f} kg into {target.location_id} which "
            f"carries {target.capacity_weight_kg:.2f} kg."
        )
    return PASS, ""


def _check_unit_weight(item: Item, target: Location) -> (str, str):
    if item.unit_weight_kg is None or target.max_unit_weight_kg is None:
        return INSUFFICIENT, (
            f"Maximum unit weight cannot be verified for {item.sku} in {target.location_id}."
        )
    if item.unit_weight_kg > target.max_unit_weight_kg + 1e-9:
        return FAIL, (
            f"A single unit of {item.sku} weighs {item.unit_weight_kg:.2f} kg; "
            f"{target.location_id} accepts at most {target.max_unit_weight_kg:.2f} kg per unit."
        )
    return PASS, ""


def _check_temperature(item: Item, target: Location) -> (str, str):
    if item.temperature_zone is None or target.temperature_zone is None:
        return INSUFFICIENT, (
            f"Temperature zone is unknown for {item.sku} or {target.location_id}."
        )
    if item.temperature_zone != target.temperature_zone:
        return FAIL, (
            f"{item.sku} requires temperature zone '{item.temperature_zone}'; "
            f"{target.location_id} is '{target.temperature_zone}'."
        )
    return PASS, ""


def _check_hazard(item: Item, target: Location) -> (str, str):
    if item.hazard_class is None or not target.hazard_classes_allowed:
        return INSUFFICIENT, (
            f"Hazard class is unknown for {item.sku} or {target.location_id} declares no "
            "allowed hazard classes."
        )
    if item.hazard_class not in target.hazard_classes_allowed:
        return FAIL, (
            f"{target.location_id} does not allow hazard class '{item.hazard_class}' "
            f"(allowed: {', '.join(target.hazard_classes_allowed)})."
        )
    return PASS, ""


def _check_handling_mode(item: Item, target: Location) -> (str, str):
    if item.handling_mode is None or not target.allowed_modes:
        return INSUFFICIENT, (
            f"Handling mode is unknown for {item.sku} or {target.location_id} declares no "
            "allowed modes."
        )
    if item.handling_mode not in target.allowed_modes:
        return FAIL, (
            f"{item.sku} is handled with '{item.handling_mode}'; {target.location_id} allows "
            f"{', '.join(target.allowed_modes)}."
        )
    return PASS, ""


def _check_zone(
    source: Location, target: Location, restrict_same_zone: bool
) -> (str, str):
    if source.location_type != "pick" or target.location_type != "pick":
        return FAIL, (
            f"Schema 1.0 only swaps pick locations; {source.location_id} is "
            f"'{source.location_type}' and {target.location_id} is '{target.location_type}'."
        )
    if restrict_same_zone:
        if source.zone_id is None or target.zone_id is None:
            return INSUFFICIENT, (
                "Same-zone restriction is active but at least one location has no zone_id."
            )
        if source.zone_id != target.zone_id:
            return FAIL, (
                f"Same-zone restriction is active: {source.location_id} is in "
                f"{source.zone_id} and {target.location_id} is in {target.zone_id}."
            )
    return PASS, ""


def _check_level(source: Location, target: Location, allow_level_change: bool) -> (str, str):
    if source.level is None or target.level is None:
        return INSUFFICIENT, (
            f"Rack level is unknown for {source.location_id} or {target.location_id}."
        )
    if source.level != target.level and not allow_level_change:
        return FAIL, (
            f"The swap changes rack level ({source.level} -> {target.level}). Enable "
            "config.allow_level_change after confirming ergonomics on site."
        )
    return PASS, ""


def _check_fixed(
    item_a: Item, item_b: Item, location_a: Location, location_b: Location
) -> (str, str):
    pinned: List[str] = []
    if location_a.fixed:
        pinned.append(f"location {location_a.location_id} is fixed")
    if location_b.fixed:
        pinned.append(f"location {location_b.location_id} is fixed")
    if item_a.fixed_location:
        pinned.append(f"item {item_a.sku} is pinned to its location")
    if item_b.fixed_location:
        pinned.append(f"item {item_b.sku} is pinned to its location")
    if pinned:
        return FAIL, "; ".join(pinned) + "."
    return PASS, ""


def check_pair_swap(
    dataset: Dataset,
    sku_a: str,
    sku_b: str,
) -> ConstraintResult:
    """Evaluate every constraint for swapping the pick locations of two SKUs."""
    result = ConstraintResult()
    items = dataset.items_by_sku()
    locations = dataset.locations_by_id()
    assignments = dataset.pick_assignments()

    item_a = items.get(sku_a)
    item_b = items.get(sku_b)
    assignment_a = assignments.get(sku_a)
    assignment_b = assignments.get(sku_b)
    location_a = locations.get(assignment_a.location_id) if assignment_a else None
    location_b = locations.get(assignment_b.location_id) if assignment_b else None

    if not all((item_a, item_b, assignment_a, assignment_b, location_a, location_b)):
        for name in CHECK_ORDER:
            result.checks[name] = INSUFFICIENT
        result.reasons["_input"] = (
            "Both SKUs need a known item master record and exactly one active pick "
            "assignment pointing at a known location."
        )
        return result

    restrict_same_zone = bool(dataset.config.get("restrict_swaps_to_same_zone", False))
    allow_level_change = bool(dataset.config.get("allow_level_change", False))
    units_a = _units_for(assignment_a)
    units_b = _units_for(assignment_b)

    # Item A moves into location B, item B moves into location A.
    pairs = (
        (item_a, location_b, units_a),
        (item_b, location_a, units_b),
    )

    checks: Dict[str, List[str]] = {name: [] for name in CHECK_ORDER}
    for item, target, units in pairs:
        for name, (status, reason) in (
            ("volume", _check_volume(item, target, units)),
            ("weight", _check_weight(item, target, units)),
            ("unit_weight", _check_unit_weight(item, target)),
            ("temperature", _check_temperature(item, target)),
            ("hazard", _check_hazard(item, target)),
            ("handling_mode", _check_handling_mode(item, target)),
        ):
            checks[name].append(status)
            if reason and name not in result.reasons:
                result.reasons[name] = reason
            elif reason:
                result.reasons[name] = result.reasons[name] + " " + reason

    zone_status, zone_reason = _check_zone(location_a, location_b, restrict_same_zone)
    checks["zone"].append(zone_status)
    if zone_reason:
        result.reasons["zone"] = zone_reason

    level_status, level_reason = _check_level(location_a, location_b, allow_level_change)
    checks["level"].append(level_status)
    if level_reason:
        result.reasons["level"] = level_reason

    fixed_status, fixed_reason = _check_fixed(item_a, item_b, location_a, location_b)
    checks["fixed"].append(fixed_status)
    if fixed_reason:
        result.reasons["fixed"] = fixed_reason

    for name in CHECK_ORDER:
        result.checks[name] = _worst(*checks[name]) if checks[name] else INSUFFICIENT
    return result
