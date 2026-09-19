# Intralogistics flow analysis — report outline

Begin with the dataset ID, engine version, schema version and status from
analysis.json. Every claim must trace back to a computed artifact.

## 1. Summary

Report the period and time zone, reconstructed and total orders, route coverage,
total modelled distance, median and p90 distance per order, and recommendation
count. Use the engine's units and provenance labels. If a value is unavailable,
state the missing evidence explicitly; do not invent a value.

## 2. Data quality

What was reconstructed, what was excluded, and why. Every validation issue with
its code, severity, field and affected records.

## 3. Flow metrics

Totals, per-order distribution, distance per pick line, zone transitions, the
busiest aisle segments and the longest orders.

State the percentile method: linear interpolation between closest ranks.

## 4. ABC / XYZ classification

ABC basis and thresholds, then the table. If XYZ was not computed, print
`XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS` and state plainly that the absence of a
class is not a class.

## 5. Leading locations

Visits, visit share, attributable distance, distance share, distance from the
start node. Repeat the attribution convention: the arriving leg is charged to the
location reached.

## 6. Co-pick affinities

Co-pick count, support, both confidences, lift. Close with: co-occurrence
describes how often two items appear in the same order; it does not establish
causality and does not by itself justify relocating either item.

## 7. Visualisation

`flow-map.svg` and `spaghetti.svg`, with a one-line reading key for each.

## 8. Simulated recommendations

One row per accepted swap: origin, destination, baseline, simulated,
**estimated pick-distance reduction** in metres and percent, affected orders, and
the result of all nine constraint checks. Then the rejected candidates and the
check that blocked each one.

## 9. Assumptions

At minimum: the historical pick sequence is unchanged; order composition is
unchanged; travel follows the shortest permitted path on the declared graph.

## 10. Limitations

At minimum: pair swaps only, no global optimum; replenishment and relocation
effort excluded; congestion not modelled; distances declared, not measured; no
individual performance measure.

## 11. Field validation actions

What a planner should check on site before anything is moved, and what data would
close the remaining evidence gaps.

---

Fakt · Berechnung · Simulation · Hypothese · Fehlende Evidenz — keep them
labelled and keep them apart.

No individual worker data is processed, stored or reported. The unit of analysis
is the order, the route, the item, the location and the zone.
