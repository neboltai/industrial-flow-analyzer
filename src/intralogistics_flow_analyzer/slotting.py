"""Explainable pair-swap candidate generation.

This is deliberately *not* a global optimiser. Schema 1.0 produces a ranked list
of individually simulated, constraint-checked swaps between two pick locations,
each of which a planner can accept or reject on its own merits.

Candidate generation
--------------------
1. Rank SKUs by pick-line frequency.
2. Rank pick locations by graph distance from ``route_start_node``.
3. Form pairs (a, b) where a is picked more often than b and sits further from
   the start node than b. Only those pairs can reduce travel under an unchanged
   pick sequence, so nothing else is worth simulating.
4. Order candidates by the crude gain proxy ``(freq_a - freq_b) * (dist_a -
   dist_b)`` and cap the list at ``config.maximum_candidate_swaps``.

Every surviving candidate is then constraint-checked and fully simulated by
replaying the historical orders. The proxy only decides *what gets simulated*;
it never appears in a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .constraints import STATUS_BLOCKED, STATUS_INSUFFICIENT, STATUS_OK, check_pair_swap
from .graph import Graph
from .models import Dataset
from .routing import RoutingResult, build_routes
from .simulation import (
    SIMULATION_ASSUMPTIONS,
    SIMULATION_LIMITATIONS,
    Move,
    build_override,
    compare_scenarios,
)

#: Wording rule. The engine never emits "saving", "guaranteed" or "optimal".
REDUCTION_LABEL = "estimated pick-distance reduction"


@dataclass
class SlottingResult:
    recommendations: List[Dict[str, Any]] = field(default_factory=list)
    rejected_candidates: List[Dict[str, Any]] = field(default_factory=list)
    evaluated_candidates: int = 0
    generated_candidates: int = 0
    thresholds: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recommendations": list(self.recommendations),
            "rejected_candidates": list(self.rejected_candidates),
            "generated_candidates": self.generated_candidates,
            "evaluated_candidates": self.evaluated_candidates,
            "thresholds": dict(self.thresholds),
            "method": (
                "Independent pair swaps between two pick locations, each simulated by "
                "replaying the historical orders with an unchanged pick sequence. "
                "This is not a global layout optimum."
            ),
        }

    def moves_document(self) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "note": (
                "Moves proposed by 'ifa recommend'. Each entry was simulated "
                "independently; applying several at once has not been simulated."
            ),
            "moves": [
                {
                    "type": "pair_swap",
                    "sku_a": rec["sku_a"],
                    "sku_b": rec["sku_b"],
                    "recommendation_id": rec["recommendation_id"],
                }
                for rec in self.recommendations
            ],
        }


def _pick_line_counts(dataset: Dataset) -> Dict[str, int]:
    counts: Dict[str, int] = {item.sku: 0 for item in dataset.items}
    for pick in dataset.picks:
        if pick.sku in counts:
            counts[pick.sku] += 1
    return counts


def _location_distances(dataset: Dataset, graph: Graph) -> Dict[str, Optional[float]]:
    start_node = dataset.config.get("route_start_node")
    mode = str(dataset.config.get("default_mode") or "pedestrian")
    distances: Dict[str, Optional[float]] = {}
    for location in dataset.locations:
        distances[location.location_id] = (
            graph.distance(str(start_node), location.access_node_id, mode) if start_node else None
        )
    return distances


def generate_candidates(
    dataset: Dataset, graph: Graph, maximum: Optional[int] = None
) -> List[Tuple[str, str]]:
    """Ordered candidate pairs ``(sku_far_frequent, sku_near_infrequent)``."""
    if maximum is None:
        maximum = int(dataset.config.get("maximum_candidate_swaps") or 500)
    counts = _pick_line_counts(dataset)
    distances = _location_distances(dataset, graph)
    assignments = dataset.pick_assignments()
    locations = dataset.locations_by_id()

    entries: List[Tuple[str, float, int]] = []
    for sku, assignment in assignments.items():
        location = locations.get(assignment.location_id)
        if location is None or location.location_type != "pick":
            continue
        distance = distances.get(assignment.location_id)
        if distance is None:
            continue
        entries.append((sku, float(distance), counts.get(sku, 0)))

    scored: List[Tuple[float, str, str]] = []
    for sku_a, distance_a, frequency_a in entries:
        for sku_b, distance_b, frequency_b in entries:
            if sku_a == sku_b:
                continue
            if frequency_a <= frequency_b:
                continue
            if distance_a <= distance_b:
                continue
            proxy = (frequency_a - frequency_b) * (distance_a - distance_b)
            scored.append((proxy, sku_a, sku_b))

    scored.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    return [(sku_a, sku_b) for _, sku_a, sku_b in scored[:maximum]]


def recommend(
    dataset: Dataset,
    graph: Graph,
    baseline: Optional[RoutingResult] = None,
    top: Optional[int] = None,
) -> SlottingResult:
    """Constraint-check and simulate every candidate; rank the surviving swaps."""
    baseline = baseline or build_routes(dataset, graph)
    minimum_improvement = float(dataset.config.get("minimum_improvement_percent") or 0.0)
    maximum_recommendations = int(
        top if top is not None else (dataset.config.get("maximum_recommendations") or 10)
    )
    maximum_candidates = int(dataset.config.get("maximum_candidate_swaps") or 500)

    candidates = generate_candidates(dataset, graph, maximum_candidates)
    counts = _pick_line_counts(dataset)
    distances = _location_distances(dataset, graph)
    assignments = dataset.pick_assignments()

    result = SlottingResult(
        generated_candidates=len(candidates),
        thresholds={
            "minimum_improvement_percent": minimum_improvement,
            "maximum_recommendations": maximum_recommendations,
            "maximum_candidate_swaps": maximum_candidates,
            "restrict_swaps_to_same_zone": bool(
                dataset.config.get("restrict_swaps_to_same_zone", False)
            ),
            "allow_level_change": bool(dataset.config.get("allow_level_change", False)),
        },
    )

    accepted: List[Dict[str, Any]] = []
    for sku_a, sku_b in candidates:
        location_a = assignments[sku_a].location_id
        location_b = assignments[sku_b].location_id
        constraint = check_pair_swap(dataset, sku_a, sku_b)
        base_record = {
            "type": "pair_swap",
            "sku_a": sku_a,
            "from_a": location_a,
            "to_a": location_b,
            "sku_b": sku_b,
            "from_b": location_b,
            "to_b": location_a,
            "pick_lines_a": counts.get(sku_a, 0),
            "pick_lines_b": counts.get(sku_b, 0),
            "distance_from_start_a_m": distances.get(location_a),
            "distance_from_start_b_m": distances.get(location_b),
            "constraint_checks": constraint.to_dict(),
            "constraint_reasons": dict(constraint.reasons),
        }

        if constraint.status != STATUS_OK:
            base_record["status"] = (
                STATUS_INSUFFICIENT
                if constraint.status == STATUS_INSUFFICIENT
                else STATUS_BLOCKED
            )
            base_record["rejected_because"] = (
                constraint.failed_checks() or constraint.insufficient_checks()
            )
            result.rejected_candidates.append(base_record)
            continue

        move = Move(move_type="pair_swap", sku_a=sku_a, sku_b=sku_b)
        override = build_override(dataset, [move])
        comparison = compare_scenarios(dataset, graph, baseline, override, [move], constraint)
        result.evaluated_candidates += 1

        record = dict(base_record)
        record.update(
            {
                "baseline_distance_m": round(comparison.baseline_distance_m, 3),
                "simulated_distance_m": round(comparison.simulated_distance_m, 3),
                "estimated_reduction_m": round(comparison.estimated_reduction_m, 3),
                "estimated_reduction_percent": (
                    None
                    if comparison.estimated_reduction_percent is None
                    else round(comparison.estimated_reduction_percent, 4)
                ),
                "affected_orders": comparison.affected_orders,
                "affected_order_ids": comparison.affected_order_ids,
                "assumptions": list(SIMULATION_ASSUMPTIONS),
                "limitations": list(SIMULATION_LIMITATIONS),
                "measure": REDUCTION_LABEL,
            }
        )

        percent = comparison.estimated_reduction_percent
        if percent is None or percent < minimum_improvement:
            record["status"] = "no_material_improvement"
            record["rejected_because"] = ["minimum_improvement_percent"]
            result.rejected_candidates.append(record)
            continue

        record["status"] = "accepted"
        accepted.append(record)

    accepted.sort(
        key=lambda r: (
            -(r["estimated_reduction_m"] or 0.0),
            r["sku_a"],
            r["sku_b"],
        )
    )

    deduplicated: List[Dict[str, Any]] = []
    used: set = set()
    for record in accepted:
        if record["sku_a"] in used or record["sku_b"] in used:
            duplicate = dict(record)
            duplicate["status"] = "superseded_by_higher_ranked_swap"
            duplicate["rejected_because"] = ["sku_already_moved_in_this_run"]
            result.rejected_candidates.append(duplicate)
            continue
        used.add(record["sku_a"])
        used.add(record["sku_b"])
        deduplicated.append(record)

    for index, record in enumerate(deduplicated[:maximum_recommendations], start=1):
        record["recommendation_id"] = f"SWAP-{index:03d}"
        result.recommendations.append(record)

    for record in deduplicated[maximum_recommendations:]:
        overflow = dict(record)
        overflow["status"] = "beyond_maximum_recommendations"
        overflow["rejected_because"] = ["maximum_recommendations"]
        result.rejected_candidates.append(overflow)

    result.rejected_candidates.sort(
        key=lambda r: (r.get("status", ""), r.get("sku_a", ""), r.get("sku_b", ""))
    )
    return result
