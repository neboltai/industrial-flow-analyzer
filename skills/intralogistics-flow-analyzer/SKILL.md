---
name: intralogistics-flow-analyzer
description: >
  Analyze warehouse and intralogistics pick flows, calculate route distances,
  generate spaghetti diagrams and evaluate constraint-safe storage-location
  swaps. Use when the user provides a warehouse layout, pick history, item
  master or storage-location data and asks about travel distance, material
  flow, slotting, ABC/XYZ, co-pick affinity or warehouse-layout improvement.
---

# Intralogistics Flow & Slotting Analyzer

Pickwege sichtbar machen. Lagerplätze datenbasiert verbessern.
**Der Algorithmus rechnet. Die KI ordnet ein.**

The deterministic engine computes every number. This skill decides what to run,
in which order, and how to phrase the result so that a fact is never confused
with a simulation.

## What you are actually driving

Five separable things. Say which one you are using, because they do not have the
same reach:

* **the Python engine and its CLI (`ifa`)** — deterministic, local, and able to
  analyse *real structured warehouse exports* on the user's own machine;
* **this Skill** — the workflow and the vocabulary below;
* **the plugin manifests** — packaging for a Codex or Claude runtime;
* **the demonstration MCP server** — six read-only tools that reach *only* the
  fictional datasets under `mcp/mock_data`, by design;
* **an industrial implementation** — verified mappings, read-only WMS/ERP
  connections, site rules, access control and validation on the floor. Out of
  scope here.

So: if the user hands you their own export, analyse it with the CLI. If they have
no data, run the fictional demonstration dataset through the MCP server. Never
suggest the MCP server can reach their warehouse, and never imply the engine
cannot handle real data — it can, locally.

## What this is not

Not a WMS. Not stock or inventory management. Not a location-reservation system.
Not individual worker monitoring. Not a global layout optimiser. Not a ready-made
connector to an ERP or WMS. It never modifies the source data. It is a local
analysis and decision-support layer on top of structured exports.

## Workflow

Follow these eleven steps in order. Do not skip ahead, and stop where the step
says stop.

1. **Identify the data.** Establish which of the six blocks the user actually
   has: layout nodes, aisle edges, storage locations, item master, location
   assignments, pick history. Name what is missing. See
   `references/data-contract.md`.
2. **Normalise without guessing.** Write an explicit `mapping.json`: file names,
   column correspondences, units, permitted defaults and purely syntactic
   transforms. Declare `meta.sequence_basis` and `meta.distance_basis`; ask the
   user where the pick sequence comes from rather than assuming. Propose the
   mapping and ask them to confirm it. Then run `ifa normalize`. If a
   correspondence is ambiguous, ask the normalisation question instead of
   choosing an answer.
3. **Validate.** Run `ifa validate`. Report the status (`ok`, `partial`,
   `insufficient_evidence`, `data_error`) and the issues verbatim.
4. **Stop on a blocking error.** With exit code 1 or status `data_error`, do not
   analyse, do not estimate, do not recommend. List what must be corrected.
5. **Compute routes.** `ifa analyze` reconstructs each order on the aisle graph
   using `pick_sequence`. Orders that cannot be routed are named, not smoothed
   away.
6. **Compute metrics.** Distance totals, per-order distribution, per-location and
   per-SKU attribution, edge traversals, zone transitions.
7. **Classify.** ABC on the configured basis; XYZ only when enough time buckets
   exist. See `references/methodology.md`.
8. **Check constraints.** Nine hard checks per candidate swap. An unknown value
   blocks the move. See `references/slotting-rules.md`.
9. **Simulate.** Replay the historical orders against the modified slotting and
   compare against the baseline. Report the reduction as an estimate.
10. **Separate results from assumptions.** Every statement is tagged
    `observed`, `calculated`, `simulated` or `hypothesis`, with its assumptions
    and limitations attached. See `references/evidence-rules.md`.
11. **Produce the report.** `analysis.json`, `recommendations.csv`, `report.md`,
    `report.html`, `flow-map.svg`, `spaghetti.svg`. See
    `references/output-contract.md` and `assets/report-template.md`.

## Commands

```bash
ifa normalize --input <csv-dir> --mapping <mapping.json> --output <dataset.json>
ifa validate  --dataset <dataset.json>
ifa analyze   --dataset <dataset.json> --output <dir>
ifa recommend --dataset <dataset.json> --top 10 --output <dir>
ifa simulate  --dataset <dataset.json> --moves <moves.json> --output <dir>
ifa run-evals
```

Every command also runs as `python -m intralogistics_flow_analyzer <command>`.

## Absolute rules

* Never invent a layout. Without nodes and edges there is no route analysis.
* Never invent a distance. Use `distance_m` from the declared edges.
* Never substitute a straight-line distance when a graph exists. A straight line
  crosses racks.
* Never call a modelled distance a measurement. The engine computes the shortest
  permitted path on the declared graph, in the given sequence. Say "modelled
  graph distance", "reconstructed shortest-path distance", "modelled segment
  traversals". Never "actual walked distance" unless real movement data proves it.
* Never present a route as what was walked when `sequence_basis` is `planned` or
  `unknown`. Report the value, and say what it allows.
* Never use file row order as a pick sequence. Without `pick_sequence`, route
  reconstruction is unavailable and the status is `partial` or
  `insufficient_evidence`.
* Never recommend a move while a critical constraint value is unknown. Return
  `insufficient_constraint_data`.
* Never call an overlap of traversals congestion without time-stamped evidence.
  Say the concentration is high and that congestion must be verified on site.
* Never present a simulated reduction as a realised saving. The wording is
  "estimated pick-distance reduction".
* Never evaluate a person. Drop `employee_id`, `worker_name`, `picker_name` and
  their equivalents; never reproduce them; never compare individuals.
* Never write to a WMS, an ERP or any external system.
* Never present recommendations as a global optimum. They are individually
  simulated pair swaps that a planner accepts or rejects one at a time.

## Provenance, every time

Two `meta` fields govern how a figure may be described. State both in the report
and in what you say:

| `sequence_basis` | What a route may be called |
| --- | --- |
| `scan_confirmed` | confirmed at each pick — the strongest basis this edition accepts |
| `observed` | taken from movement records |
| `planned` | the system's intended order; the walked order may have differed |
| `unknown` | not declared; describe the route as modelled from an unstated sequence |

`distance_basis` is `declared_graph_shortest_path` in schema 1.0. A dataset that
omits either field is read as `unknown` and
`declared_graph_shortest_path` respectively — that is deliberate and
conservative, not an oversight to paper over.

## How to phrase a result

Good:

> Location L-018 accounts for 14 % of recorded visits and sits in the quartile of
> pick locations furthest from the start node.

> SKU A-104 and SKU B-208 appear together in 38 % of the orders containing A-104.

> Aisle segment E-07 carries 31 % of all modelled segment traversals.

> Swapping A-104 with C-902 gives an estimated pick-distance reduction of
> 1 560 m (12.4 %) over 48 historical orders, assuming the scan-confirmed pick
> sequence is unchanged. Distances are modelled on the declared graph.
> Relocation effort and replenishment travel are not included.

Not acceptable:

> This aisle is congested.
> These two items must be stored together.
> This change will save 1 560 m.
> This is the optimal layout.
> The pickers actually walked 1 560 m.

## When evidence is thin

Say so, name what is missing, and say what would close the gap: a longer export
period, `pick_sequence` on every line, the missing capacity or temperature
fields, or time-stamped movement data. An honest "insufficient evidence" is a
valid deliverable.
