# Methodology

Every formula the engine uses, written out so that any number in a report can be
recomputed by hand.

## Graph and shortest paths

The aisle graph is directed and weighted. A bidirectional edge yields two arcs
with the same `edge_id` and `distance_m`. An arc is traversable by a travel mode
only if the mode appears in the edge's `allowed_modes`.

Shortest paths use Dijkstra over a binary heap. The full single-source tree is
cached per `(source, mode)` pair, so repeated leg lookups inside and across
orders cost nothing after the first expansion. Ties are broken by the lower
`edge_id`, which makes repeated runs byte-identical.

## Route reconstruction

Reconstruction produces a **modelled** distance, never a measured one. The
sequence comes from the dataset, and `meta.sequence_basis` records where that
sequence came from; `meta.distance_basis` records that the figure is a
shortest-path computation on the declared graph. Both travel into every report.

For each order:

```
route_start_node -> access node of pick 1 -> ... -> access node of pick n -> route_end_node
```

The sequence comes exclusively from `pick_sequence`. Consecutive picks sharing an
access node produce a zero-length leg: they count as pick lines and add no
travel.

Field names are unchanged from 0.1.0 so the contract does not break: `total_distance_m` is a modelled distance in metres.

Per order the engine records `total_distance_m`, `distance_between_picks_m`,
`edge_traversals`, `zone_transitions`, `number_of_picks`, `number_of_lines` and
`unreachable_segments`.

An order with an unreachable leg is **not** a valid complete route. Its distance
is `null`, it is counted under `unreachable_order_count`, and it enters no
average.

A `zone_transition` is counted whenever the zone of the current stop differs from
the zone of the previous stop, over the sequence start node, pick access nodes,
end node.

## Distance attribution

The leg that *arrives* at a location is charged to that location. The final
return leg is charged to the last location of the order. This is an accounting
convention that makes per-location contributions sum exactly to the total
distance; it is not a physical measurement, and it is stated as such in the
report.

## Percentiles

Sorted ascending, `rank = p / 100 * (n - 1)`, linear interpolation between the
two neighbouring ranks. For `n = 1` the single value is returned. This is the
"linear interpolation between closest ranks" method.

Example on `[10, 20, 30, 40]`: `p90` gives `rank = 2.7`, that is
`30 + 0.7 * (40 - 30) = 37`.

The median uses `statistics.median`; the mean uses `statistics.fmean`.

## ABC

Items are ranked by descending contribution on `config.abc_basis`:

* `pick_lines` — number of pick lines (default);
* `units` — sum of quantities;
* `orders` — number of distinct orders containing the item.

The cumulative share decides the class. The item that *crosses* a threshold
belongs to the class it completes, so class A always reaches at least the
configured share. Ties on the basis value are broken by SKU, which keeps the
ranking reproducible. An item with zero demand is class C.

Thresholds default to `A = 0.80`, `B = 0.95` and appear in `analysis.thresholds`.

## XYZ

XYZ describes demand *variability*, never demand size.

Demand is bucketed by calendar day (or ISO week). Every bucket inside the
declared period counts, including buckets with no demand: a day without a pick is
real information about variability.

```
cv = population standard deviation of per-bucket pick lines / mean of that series
```

Default classes: `cv <= 0.5` is X, `cv <= 1.0` is Y, above that is Z.

If the number of buckets is below `config.xyz_minimum_buckets`, nothing is
classified and the engine returns the marker
`XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS`. Missing data is never silently turned
into class Z. An item with zero demand receives no class and a note saying
variability is undefined.

## Co-pick affinity

For every unordered pair present in at least one common order:

```
co_pick_count      orders containing both items
support            co_pick_count / total_orders
confidence_a_to_b  co_pick_count / orders_containing_a
confidence_b_to_a  co_pick_count / orders_containing_b
lift               support / (support_a * support_b)
```

Every denominator is checked before dividing; an undefined value is reported as
`null`, never as zero. Results are filtered by
`affinity_minimum_co_picks` and `affinity_minimum_support`, and ordered by
co-pick count first, because lift computed over a handful of orders is volatile.

A high affinity means only that two items appear frequently in the same orders.

## Slotting candidates

1. Rank SKUs by pick-line frequency.
2. Rank pick locations by graph distance from `route_start_node` in the default
   mode.
3. Form pairs `(a, b)` where `a` is picked more often than `b` **and** sits
   further from the start node than `b`. Under an unchanged pick sequence no
   other pair can reduce travel, so nothing else is worth simulating.
4. Order candidates by the proxy `(freq_a - freq_b) * (dist_a - dist_b)` and cap
   the list at `config.maximum_candidate_swaps`.

The proxy decides only *what gets simulated*. It never appears in a result.

Each surviving candidate is constraint-checked, then fully simulated by replaying
the historical orders. Candidates below `minimum_improvement_percent` are
reported as `no_material_improvement`. Within one run a SKU is moved at most
once; a lower-ranked swap touching an already-moved SKU is reported as
`superseded_by_higher_ranked_swap`.

## Simulation

The source dataset is never modified. A move list produces an in-memory
`sku -> location_id` override, and routing replays exactly the same orders in
exactly the same pick sequence.

```
estimated_reduction_m       = baseline_distance_m - simulated_distance_m
estimated_reduction_percent = 100 * estimated_reduction_m / baseline_distance_m
```

Both totals are computed over the orders routable in **both** scenarios, so the
comparison is like for like.

Assumptions carried on every simulated figure: the historical pick sequence is
unchanged, order composition is unchanged, graph distances are unchanged.

Excluded by design: replenishment travel, put-away travel, the one-off effort of
relocating stock, congestion, queueing and shift patterns.
