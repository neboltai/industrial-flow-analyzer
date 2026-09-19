# Slotting rules

Schema 1.0 proposes explainable pair swaps. It does not solve a global
optimisation problem and never claims to.

## Scope

Allowed in 0.1.2:

* swapping the pick locations of exactly two SKUs.

Not allowed in 0.1.2:

* creating, splitting or merging a location;
* moving more than two SKUs in one recommendation;
* touching a `fixed` location or a pinned item;
* optimising reserve locations;
* any physical move, and any write to a WMS.

## The nine hard checks

Each is evaluated in both directions — item A into location B, and item B into
location A — and yields `pass`, `fail` or `insufficient_data`.

| Check | Rule |
| --- | --- |
| `volume` | `unit_volume_m3 * stored_units <= capacity_volume_m3` of the target |
| `weight` | `unit_weight_kg * stored_units <= capacity_weight_kg` of the target |
| `unit_weight` | `unit_weight_kg <= max_unit_weight_kg` of the target |
| `temperature` | item `temperature_zone` equals the target's `temperature_zone` |
| `hazard` | item `hazard_class` is in the target's `hazard_classes_allowed` |
| `handling_mode` | item `handling_mode` is in the target's `allowed_modes` |
| `zone` | both locations are of type `pick`; optional same-zone restriction |
| `level` | rack level may change only when `config.allow_level_change` is true |
| `fixed` | neither location is `fixed` and neither item has `fixed_location` |

`stored_units` is `max_units` when declared, otherwise `current_units`.

## Missing data blocks the move

An unknown value is never treated as a pass. If any check returns
`insufficient_data` and none returns `fail`, the candidate is rejected with
status `insufficient_constraint_data` and the missing fields are named. A `fail`
outranks an `insufficient_data`: a candidate with both is reported as a
constraint violation.

## Evaluation order

1. Identify frequently picked SKUs sitting far from the start node.
2. Identify less frequently picked SKUs sitting closer.
3. Generate candidate pairs (see `methodology.md`).
4. Check all nine constraints.
5. Apply the swap virtually.
6. Replay the historical orders.
7. Recompute the distance.
8. Compare against the baseline.
9. Rank by the calculated reduction.

## What a recommendation must show

```json
{
  "recommendation_id": "SWAP-001",
  "type": "pair_swap",
  "sku_a": "SKU-A",
  "from_a": "L-018",
  "to_a": "L-003",
  "sku_b": "SKU-B",
  "from_b": "L-003",
  "to_b": "L-018",
  "baseline_distance_m": 12540.0,
  "simulated_distance_m": 10980.0,
  "estimated_reduction_m": 1560.0,
  "estimated_reduction_percent": 12.44,
  "affected_orders": 48,
  "constraint_checks": {
    "volume": "pass",
    "weight": "pass",
    "unit_weight": "pass",
    "temperature": "pass",
    "hazard": "pass",
    "handling_mode": "pass",
    "zone": "pass",
    "level": "pass",
    "fixed": "pass"
  },
  "assumptions": ["Historical pick sequence remains unchanged."],
  "limitations": ["Replenishment travel and relocation effort are not included."],
  "measure": "estimated pick-distance reduction"
}
```

The result of every single check is visible, including the checks that passed.

## Vocabulary

The term **guaranteed saving** is never used. The measure is always
**estimated pick-distance reduction**, and the recommendation set is never
described as an optimum.

## Rejection statuses

| Status | Meaning |
| --- | --- |
| `constraint_violation` | at least one hard check failed |
| `insufficient_constraint_data` | at least one required value is unknown |
| `no_material_improvement` | below `minimum_improvement_percent` |
| `superseded_by_higher_ranked_swap` | a SKU is already moved by a better swap in this run |
| `beyond_maximum_recommendations` | valid, but outside the requested top N |

Rejected candidates are reported, not hidden: a planner learns as much from a
blocked move as from an accepted one.
