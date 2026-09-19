# Evidence rules

The engine and the skill both apply these rules. They exist so that a reader can
always tell what was counted, what was computed, what was simulated and what is
still an open question.

## The ten rules

1. A high pick frequency is not evidence of a badly placed item.
2. A long modelled route is not evidence of avoidable travel.
3. A heavily traversed aisle segment in the model is not evidence of congestion.
4. Co-occurrence of two items is not evidence that they belong next to each other.
5. A simulated reduction is not a realised saving.
6. The cost of physically relocating stock stays separate from any potential gain.
7. Replenishment distance stays separate from picking distance.
8. No slotting change is proposed while a critical constraint value is missing.
9. Every scenario names the historical orders it replayed.
10. Every conclusion traces back to a record or a calculation in the report.

## Modelled, not measured

The engine computes the shortest permitted path on the **declared** aisle graph,
between consecutive pick access nodes, in the sequence the dataset supplies. It
never observes movement.

Two `meta` fields make that explicit, and both appear in every report.

| `sequence_basis` | Meaning | What it supports |
| --- | --- | --- |
| `scan_confirmed` | the sequence was confirmed by a scan at each pick | statements about the order actually picked |
| `observed` | the sequence comes from movement records | statements about the order actually picked |
| `planned` | the sequence is the system's intention | statements about the intended order only |
| `unknown` | provenance not declared | modelled figures, described as such |

`distance_basis` has one value in schema 1.0: `declared_graph_shortest_path`.

A document written before these fields existed loads unchanged and is read as
`unknown` / `declared_graph_shortest_path`.

Use: *modelled graph distance*, *reconstructed shortest-path distance*,
*modelled segment traversals*. Do not use *actual walked distance* unless real
movement data is present, which schema 1.0 cannot represent.

## The five labels

Every report separates:

| Claim type | German term | Meaning |
| --- | --- | --- |
| **Observed** | Fakt | counted directly in the input records |
| **Calculated** | Berechnung | derived from the graph and the records |
| **Simulated** | Simulation | produced by replaying history against a modified slotting |
| **Hypothesis** | Hypothese | a question raised by the data, to be verified on site |
| *Insufficient evidence* | Fehlende Evidenz | something that could not be computed, and what is missing |

Report tables use the English labels. The German terms appear once, in the
glossary that introduces the claim types.

## Evidence quality, per finding family

`evidence_quality` is a **label**, not a probability. Up to 0.1.0 a single
coverage-and-sample-size rule labelled every finding, which gave a two-order
co-pick pair the same confidence as a distance calculation. Since 0.1.1 each
family has its own deterministic rule, implemented in
`src/intralogistics_flow_analyzer/evidence.py` and tested per level.

Every finding also carries `evidence_assessment`:

```json
{
  "factors": { "routed_orders": 15, "route_coverage_percent": 100.0 },
  "site_verified": false,
  "rationale": "15 routed orders at 100.0% coverage on a 'scan_confirmed' sequence.",
  "caps_applied": []
}
```

so the label can be recomputed by hand from the factors that produced it.

### Routes, distances, locations and aisle segments

Factors: routed orders, route coverage, sequence provenance, graph completeness.

| Level | Condition |
| --- | --- |
| high | ≥ 30 routed orders **and** ≥ 95 % coverage |
| medium | ≥ 10 routed orders **and** ≥ 80 % coverage |
| low | ≥ 3 routed orders |
| insufficient | fewer |

Caps: a `sequence_basis` other than `scan_confirmed` or `observed` caps at
**medium**; any unroutable order caps at **low**.

### ABC

Factors: number of orders, length of the period, the configured basis, whether
the basis data is complete.

| Level | Condition |
| --- | --- |
| high | ≥ 30 orders **and** ≥ 28 days |
| medium | ≥ 10 orders **and** ≥ 7 days |
| low | ≥ 3 orders |
| insufficient | fewer |

Cap: incomplete basis data caps at **low**.

### XYZ

Factors: number of buckets, bucket type, share of pick lines with a valid
timestamp, the configured minimum.

| Level | Daily buckets | Weekly buckets | Timestamp rate |
| --- | --- | --- | --- |
| high | ≥ 90 | ≥ 26 | ≥ 98 % |
| medium | ≥ 28 | ≥ 12 | ≥ 90 % |
| low | ≥ the configured minimum | ≥ the configured minimum | — |

If XYZ was not computed at all, the level is **insufficient** and the marker
`XYZ_NOT_COMPUTED_INSUFFICIENT_PERIODS` is reported instead of a class.

### Co-pick affinity

Factors: total orders, co-pick count, support, the configured minimum.

| Level | Condition |
| --- | --- |
| high | ≥ 200 orders **and** ≥ 30 co-picks |
| medium | ≥ 50 orders **and** ≥ 10 co-picks |
| low | ≥ 2 co-picks and at least the configured minimum |
| insufficient | fewer |

Cap: support below 0.02 caps at **low** — a rare pair is a rare pair however
suggestive its lift looks.

### Simulation

Factors: route coverage, comparable orders, affected orders, constraint
completeness, sequence provenance, site verification.

| Level | Condition |
| --- | --- |
| high | ≥ 95 % coverage, ≥ 30 comparable orders, ≥ 30 affected orders, complete constraints |
| medium | ≥ 80 % coverage, ≥ 10 comparable orders, ≥ 10 affected orders |
| low | at least one affected order |
| insufficient | none |

Caps, and these are not negotiable:

* **an unverified scenario never reaches `high`** — it is capped at medium;
* incomplete constraint data caps at **low**;
* a `sequence_basis` of `unknown` caps at **low**.

### Constraints

Factors: number of checks evaluated, number of unknown values, site verification.

| Level | Condition |
| --- | --- |
| high | all 9 checks evaluated **and** none unknown |
| medium | all 9 checks evaluated **and** at most 2 unknown |
| low | at least one check evaluated |
| insufficient | none |

Cap: **complete constraint data that nobody verified on site never reaches
`high`** — it is capped at medium. Master data is a claim about the world, not
the world.

### Site verification

`site_verified` defaults to `false` and is `false` everywhere in the Community
Edition, which has no channel through which a verification could be recorded.
That is a property of this edition, stated rather than hidden.

### Configuring the thresholds

Every threshold above is a key of `DEFAULT_EVIDENCE_THRESHOLDS` and can be
overridden per dataset through `config.evidence_thresholds`. The values in force
are echoed into `analysis.thresholds.evidence`, so a report always carries the
rules that produced its own labels.

There is no weighted score, no composite index and no number pretending to be a
confidence. A label names a rung on the ladder above; that is all it does.

## Wording

Use:

* "accounts for X % of recorded visits"
* "carries X % of all modelled segment traversals"
* "appear together in X % of the orders containing A"
* "estimated pick-distance reduction of X m (Y %) over N historical orders"
* "the presence of congestion must be verified on site or with time-stamped data"

Never use:

* "guaranteed saving"
* "this aisle is congested"
* "these items must be stored together"
* "this will save X"
* "the optimal layout"
* "actual walked distance", when the figure is modelled

## Protection of people

The analyser produces no individual performance measure of any kind.

If an export contains `employee_id`, `worker_name`, `picker_name`,
`mitarbeiter_id`, `personalnummer`, `kommissionierer`, `bediener_id` or an
equivalent column, the normalizer:

* ignores the column by default;
* reports that it is not required for flow analysis;
* never reproduces it in a dataset or a report;
* never uses it to compare people.

Mapping such a column on purpose is refused outright. A column that merely *looks*
personal is never dropped on a guess: it is reported as
`SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED` and named in the normalisation report.

The unit of analysis is the order, the route, the item, the location and the zone.

## What belongs in a repository

No real data, no company name, no employee identifier, no token, no secret and
no customer endpoint. Every dataset shipped here carries `meta.fictional: true`,
and every report generated from one is labelled *Fictional example*.

Removing the worker columns does not make a real dataset anonymous. Order
identifiers, SKUs, volumes, layout and order profiles may all be commercially
sensitive; see `SECURITY.md`.
