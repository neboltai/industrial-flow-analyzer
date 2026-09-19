"""Co-pick affinity between items.

For every unordered pair (a, b) present in the same order at least once:

    co_pick_count     number of orders containing both
    support           co_pick_count / total_orders
    confidence_a_to_b co_pick_count / orders_containing_a
    confidence_b_to_a co_pick_count / orders_containing_b
    lift              support / (support_a * support_b)

Every denominator is checked before dividing; an undefined value is returned as
``None``, never as zero.

Interpretation rule enforced in the wording of every generated statement:
a high affinity means only that two items appear frequently in the same orders.
It is not evidence of causality and it is not on its own a reason to store them
next to each other.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

from .models import Dataset

AFFINITY_DISCLAIMER = (
    "Co-occurrence describes how often two items appear in the same order. "
    "It does not establish causality and does not by itself justify relocating "
    "either item."
)


def _order_sets(dataset: Dataset) -> Dict[str, set]:
    sets: Dict[str, set] = {}
    for pick in dataset.picks:
        sets.setdefault(pick.order_id, set()).add(pick.sku)
    return sets


def compute_affinities(
    dataset: Dataset,
    minimum_co_picks: Optional[int] = None,
    minimum_support: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return affinity rows above the configured thresholds, sorted by lift."""
    if minimum_co_picks is None:
        minimum_co_picks = int(dataset.config.get("affinity_minimum_co_picks") or 2)
    if minimum_support is None:
        minimum_support = float(dataset.config.get("affinity_minimum_support") or 0.0)

    order_sets = _order_sets(dataset)
    total_orders = len(order_sets)
    if total_orders == 0:
        return []

    sku_orders: Dict[str, int] = {}
    for skus in order_sets.values():
        for sku in skus:
            sku_orders[sku] = sku_orders.get(sku, 0) + 1

    pair_counts: Dict[Tuple[str, str], int] = {}
    for skus in order_sets.values():
        for pair in combinations(sorted(skus), 2):
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

    descriptions = {item.sku: item.description for item in dataset.items}
    pick_location = dataset.pick_location_of()

    rows: List[Dict[str, Any]] = []
    for (sku_a, sku_b), count in pair_counts.items():
        support = count / total_orders
        if count < minimum_co_picks or support < minimum_support:
            continue
        orders_a = sku_orders.get(sku_a, 0)
        orders_b = sku_orders.get(sku_b, 0)
        confidence_ab = (count / orders_a) if orders_a else None
        confidence_ba = (count / orders_b) if orders_b else None
        support_a = (orders_a / total_orders) if total_orders else None
        support_b = (orders_b / total_orders) if total_orders else None
        if support_a and support_b:
            denominator = support_a * support_b
            lift = (support / denominator) if denominator > 0 else None
        else:
            lift = None
        rows.append(
            {
                "sku_a": sku_a,
                "sku_b": sku_b,
                "description_a": descriptions.get(sku_a, ""),
                "description_b": descriptions.get(sku_b, ""),
                "pick_location_a": pick_location.get(sku_a),
                "pick_location_b": pick_location.get(sku_b),
                "co_pick_count": count,
                "orders_with_a": orders_a,
                "orders_with_b": orders_b,
                "total_orders": total_orders,
                "support": round(support, 6),
                "support_percent": round(100.0 * support, 2),
                "confidence_a_to_b": None if confidence_ab is None else round(confidence_ab, 6),
                "confidence_b_to_a": None if confidence_ba is None else round(confidence_ba, 6),
                "lift": None if lift is None else round(lift, 6),
                "interpretation": AFFINITY_DISCLAIMER,
            }
        )

    # Sorted by co-pick count first, then by lift. Lift computed on a handful of
    # orders is volatile, so the pair with the most direct evidence leads.
    rows.sort(
        key=lambda r: (
            -r["co_pick_count"],
            -(r["lift"] if r["lift"] is not None else -1.0),
            r["sku_a"],
            r["sku_b"],
        )
    )
    return rows


def affinity_thresholds(dataset: Dataset) -> Dict[str, Any]:
    return {
        "affinity_minimum_co_picks": int(dataset.config.get("affinity_minimum_co_picks") or 2),
        "affinity_minimum_support": float(dataset.config.get("affinity_minimum_support") or 0.0),
    }
