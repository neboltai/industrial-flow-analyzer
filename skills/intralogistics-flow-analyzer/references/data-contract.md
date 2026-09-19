# Data contract

The engine never works directly from ambiguous CSV columns. The path is always:

```
CSV exports -> explicit normalisation -> canonical dataset.json -> validation
            -> analysis -> simulation -> reports
```

`schemas/dataset.schema.json` is the normative definition. This page explains the
intent behind each block and what the engine refuses to infer.

## meta

| Field | Meaning |
| --- | --- |
| `dataset_id` | Stable identifier of this export. |
| `site_id` | Site or building. Never a company name. |
| `period_start`, `period_end` | ISO 8601. They define the XYZ buckets and bound the valid timestamps. |
| `distance_unit` | Must be `m` in schema 1.0. |
| `time_zone` | IANA name, for example `Europe/Berlin`. |
| `source` | Where the export came from, in free text. |
| `fictional` | `true` for every dataset shipped in this repository. |
| `sequence_basis` | `planned`, `scan_confirmed`, `observed` or `unknown`. Additive in 0.1.1; an absent field reads as `unknown`. |
| `distance_basis` | `declared_graph_shortest_path` in schema 1.0. Additive in 0.1.1; an absent field reads as that value. |

`sequence_basis` decides how a route may be *described*, never how it is
computed. Only `scan_confirmed` and `observed` support a statement about the
order actually picked; `planned` describes an intention and `unknown` describes
nothing. `distance_basis` records that the engine models distances on the
declared graph rather than measuring movement.

No real company name, no employee name, ever.

## layout.nodes

`node_id`, `x_m`, `y_m`, `node_type`, `zone_id`.

`node_type` is one of `junction`, `depot`, `receiving`, `shipping`, `packing`,
`location_access`.

Coordinates exist for drawing. They are never used to travel: a straight line
between two coordinates would cross racks and walls.

## layout.edges

`edge_id`, `from_node`, `to_node`, `distance_m`, `bidirectional`,
`allowed_modes`.

`allowed_modes` is a subset of `pedestrian`, `forklift`, `tugger`.
`distance_m` is the reference distance for every calculation. A missing graph
distance is never replaced by a straight line without being reported as an
assumption; in schema 1.0 it is simply an error.

A `bidirectional: true` edge becomes two directed arcs sharing the same
`edge_id`, so traversal counts stay comparable in both directions.

## locations

`location_id`, `access_node_id`, `zone_id`, `location_type`,
`capacity_volume_m3`, `capacity_weight_kg`, `max_unit_weight_kg`,
`temperature_zone`, `hazard_classes_allowed`, `allowed_modes`, `level`, `fixed`.

`location_type` is `pick`, `reserve`, `buffer` or `staging`.
`fixed: true` forbids any recommendation moving stock into or out of the
location, whatever the calculated benefit.

## items

`sku`, `description`, `unit_volume_m3`, `unit_weight_kg`, `temperature_zone`,
`hazard_class`, `handling_mode`, `fixed_location`.

Descriptions are neutral. `fixed_location: true` pins the item wherever it is.

## assignments

`sku`, `location_id`, `role`, `current_units`, `max_units`.

`role` is `pick` or `reserve`. Schema 1.0 allows exactly one active `pick`
assignment per SKU, and one SKU per pick location. Reserve locations are
represented but not optimised.

## picks

`pick_id`, `order_id`, `pick_sequence`, `timestamp`, `sku`, `location_id`,
`quantity`, `mode`.

Constraints:

* `pick_id` unique across the dataset;
* `order_id` mandatory;
* `pick_sequence` a positive integer, unique inside its order;
* `quantity` strictly positive;
* `location_id` and `sku` must exist.

**The row order of the file is never used as a substitute for
`pick_sequence`.** If `pick_sequence` is missing:

* frequency and affinity analyses stay valid;
* route reconstruction is unavailable for that order;
* the status becomes `partial` or `insufficient_evidence`;
* no travel distance is estimated for that order.

## config

`route_start_node`, `route_end_node`, `default_mode`, `abc_basis`,
`abc_thresholds`, `xyz_time_bucket`, `xyz_minimum_buckets`,
`minimum_improvement_percent`, `maximum_recommendations`,
`maximum_candidate_swaps`, plus the affinity and swap-policy settings.

Every threshold that influenced a result is echoed into `analysis.json` under
`thresholds`, so a reader can reproduce the analysis exactly.

## What normalisation must never guess

* a unit;
* the meaning of a column;
* the direction of an aisle;
* a temperature zone;
* a hazard class;
* the capacity of a location.

An ambiguous correspondence produces a normalisation question, not a default. An
assistant may propose a mapping; the engine executes one only after a human has
written it down.

## Worker data

Columns such as `employee_id`, `worker_name`, `picker_name`, `operator_id`, and
the German `mitarbeiter_id`, `personalnummer`, `kommissionierer`, `bediener_id`
and their equivalents are dropped before parsing, reported as unnecessary, and
never reach the canonical dataset or any report. A column that merely looks
personal is reported as `SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED` rather than
removed on a guess. The unit of analysis is the order, the
route, the item, the location and the zone — never the person.
