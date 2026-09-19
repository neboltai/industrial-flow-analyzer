"""Virtual replay of historical orders against a modified slotting.

The source dataset is never modified. A move list produces an in-memory
``sku -> location_id`` override that routing applies while replaying exactly the
same orders, in exactly the same pick sequence.

Stated assumption, repeated in every output: the historical pick sequence is
kept unchanged. A real warehouse would re-sequence picks after a slotting
change, so the simulated figure is a lower-bound estimate of the routing effect
of the move, not a forecast of realised savings.

Excluded from the comparison by design: replenishment travel, the one-off effort
of physically relocating stock, congestion, and any change in order profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .constraints import STATUS_OK, ConstraintResult, check_pair_swap
from .graph import Graph
from .models import Dataset
from .routing import RoutingResult, build_routes

SIMULATION_ASSUMPTIONS = [
    "Historical pick sequence remains unchanged.",
    "Order composition and demand mix remain unchanged.",
    "Graph distances remain unchanged after the move.",
]

SIMULATION_LIMITATIONS = [
    "Replenishment travel and relocation effort are not included.",
    "Congestion, queueing and shift patterns are not modelled.",
    "A simulated reduction is not a realised saving.",
]


class SimulationError(ValueError):
    """Raised when a move list cannot be applied in schema 1.0."""


@dataclass
class Move:
    move_type: str
    sku_a: str
    sku_b: str

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Move":
        move_type = str(raw.get("type") or "").strip()
        if move_type != "pair_swap":
            raise SimulationError(
                f"Unsupported move type {move_type!r}. Schema 1.0 simulates 'pair_swap' only: "
                "swapping the pick locations of exactly two SKUs."
            )
        sku_a = str(raw.get("sku_a") or "").strip()
        sku_b = str(raw.get("sku_b") or "").strip()
        if not sku_a or not sku_b:
            raise SimulationError("A pair_swap move requires both 'sku_a' and 'sku_b'.")
        if sku_a == sku_b:
            raise SimulationError(f"A pair_swap move requires two different SKUs ({sku_a}).")
        return cls(move_type=move_type, sku_a=sku_a, sku_b=sku_b)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.move_type, "sku_a": self.sku_a, "sku_b": self.sku_b}


def parse_moves(document: Any) -> List[Move]:
    if isinstance(document, dict):
        raw_moves = document.get("moves")
    elif isinstance(document, list):
        raw_moves = document
    else:
        raise SimulationError("A moves document must be a JSON object with 'moves', or a list.")
    if not isinstance(raw_moves, list) or not raw_moves:
        raise SimulationError("A moves document must contain a non-empty 'moves' list.")
    return [Move.from_dict(raw) for raw in raw_moves]


def build_override(dataset: Dataset, moves: Sequence[Move]) -> Dict[str, str]:
    """Return a ``sku -> location_id`` override for the requested swaps."""
    current = dict(dataset.pick_location_of())
    touched: Dict[str, str] = {}
    for move in moves:
        for sku in (move.sku_a, move.sku_b):
            if sku not in current:
                raise SimulationError(
                    f"SKU {sku} has no active pick assignment and cannot be swapped."
                )
        if move.sku_a in touched or move.sku_b in touched:
            raise SimulationError(
                "Schema 1.0 applies at most one move per SKU in a single simulation; "
                f"{move.sku_a} or {move.sku_b} appears twice."
            )
        location_a = current[move.sku_a]
        location_b = current[move.sku_b]
        touched[move.sku_a] = location_b
        touched[move.sku_b] = location_a
    override = dict(current)
    override.update(touched)
    return override


@dataclass
class ScenarioComparison:
    baseline_distance_m: float
    simulated_distance_m: float
    estimated_reduction_m: float
    estimated_reduction_percent: Optional[float]
    baseline_orders: int
    simulated_orders: int
    affected_orders: int
    affected_order_ids: List[str] = field(default_factory=list)
    per_order: List[Dict[str, Any]] = field(default_factory=list)
    constraint_status: str = STATUS_OK
    constraint_checks: Dict[str, str] = field(default_factory=dict)
    constraint_reasons: Dict[str, str] = field(default_factory=dict)
    moves: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "moves": list(self.moves),
            "constraint_status": self.constraint_status,
            "constraint_checks": dict(self.constraint_checks),
            "constraint_reasons": dict(self.constraint_reasons),
            "baseline_distance_m": round(self.baseline_distance_m, 3),
            "simulated_distance_m": round(self.simulated_distance_m, 3),
            "estimated_reduction_m": round(self.estimated_reduction_m, 3),
            "estimated_reduction_percent": (
                None
                if self.estimated_reduction_percent is None
                else round(self.estimated_reduction_percent, 4)
            ),
            "baseline_routed_orders": self.baseline_orders,
            "simulated_routed_orders": self.simulated_orders,
            "affected_orders": self.affected_orders,
            "affected_order_ids": list(self.affected_order_ids),
            "per_order": list(self.per_order),
            "assumptions": list(SIMULATION_ASSUMPTIONS),
            "limitations": list(SIMULATION_LIMITATIONS),
        }


def compare_scenarios(
    dataset: Dataset,
    graph: Graph,
    baseline: RoutingResult,
    override: Dict[str, str],
    moves: Sequence[Move],
    constraint_result: Optional[ConstraintResult] = None,
) -> ScenarioComparison:
    """Replay every order under ``override`` and compare against ``baseline``."""
    simulated = build_routes(dataset, graph, location_override=override)

    baseline_by_order = {r.order_id: r for r in baseline.complete_routes}
    simulated_by_order = {r.order_id: r for r in simulated.complete_routes}
    comparable = sorted(set(baseline_by_order) & set(simulated_by_order))

    baseline_total = sum(baseline_by_order[o].total_distance_m or 0.0 for o in comparable)
    simulated_total = sum(simulated_by_order[o].total_distance_m or 0.0 for o in comparable)
    reduction = baseline_total - simulated_total
    reduction_percent = (100.0 * reduction / baseline_total) if baseline_total > 0 else None

    per_order: List[Dict[str, Any]] = []
    affected: List[str] = []
    for order_id in comparable:
        before = baseline_by_order[order_id].total_distance_m or 0.0
        after = simulated_by_order[order_id].total_distance_m or 0.0
        delta = before - after
        if abs(delta) > 1e-9:
            affected.append(order_id)
        per_order.append(
            {
                "order_id": order_id,
                "baseline_distance_m": round(before, 3),
                "simulated_distance_m": round(after, 3),
                "delta_m": round(delta, 3),
            }
        )

    return ScenarioComparison(
        baseline_distance_m=baseline_total,
        simulated_distance_m=simulated_total,
        estimated_reduction_m=reduction,
        estimated_reduction_percent=reduction_percent,
        baseline_orders=len(baseline_by_order),
        simulated_orders=len(simulated_by_order),
        affected_orders=len(affected),
        affected_order_ids=affected,
        per_order=per_order,
        constraint_status=(constraint_result.status if constraint_result else STATUS_OK),
        constraint_checks=(constraint_result.to_dict() if constraint_result else {}),
        constraint_reasons=(dict(constraint_result.reasons) if constraint_result else {}),
        moves=[m.to_dict() for m in moves],
    )


def simulate_moves(
    dataset: Dataset,
    graph: Graph,
    moves: Sequence[Move],
    baseline: Optional[RoutingResult] = None,
) -> ScenarioComparison:
    """Check constraints, then replay history. The dataset is left untouched."""
    working = dataset.copy()
    baseline = baseline or build_routes(working, graph)

    aggregate = ConstraintResult()
    for move in moves:
        result = check_pair_swap(working, move.sku_a, move.sku_b)
        for name, status in result.checks.items():
            current = aggregate.checks.get(name)
            if current is None:
                aggregate.checks[name] = status
            elif current == "pass":
                aggregate.checks[name] = status
            elif status == "fail":
                aggregate.checks[name] = status
        for name, reason in result.reasons.items():
            aggregate.reasons.setdefault(name, reason)

    override = build_override(working, moves)
    return compare_scenarios(working, graph, baseline, override, moves, aggregate)
