# Output contract

`ifa analyze --output <dir>` writes:

| File | Content |
| --- | --- |
| `analysis.json` | the complete machine-readable result |
| `recommendations.csv` | one row per accepted swap, with every constraint check |
| `report.md` | the readable report, eleven sections |
| `report.html` | the same report as one self-contained static file |
| `flow-map.svg` | layout with traversal intensity per aisle segment |
| `spaghetti.svg` | the longest reconstructed routes |
| `spaghetti-aggregate.svg` | all routes collapsed into per-segment intensity |
| `moves.json` | the accepted swaps, ready for `ifa simulate --moves` |

`ifa recommend --output <dir>` writes `recommendations.json`,
`recommendations.csv` and `moves.json`.
`ifa simulate --output <dir>` writes `simulation.json` and `simulation.md`.

## analysis.json

```json
{
  "schema_version": "1.0",
  "engine_version": "0.1.2",
  "status": "ok",
  "meta": {},
  "issues": [],
  "data_quality": {},
  "metrics": {},
  "edge_flows": [],
  "location_metrics": [],
  "sku_metrics": [],
  "abc": [],
  "xyz": [],
  "affinities": [],
  "recommendations": [],
  "findings": [],
  "thresholds": {},
  "limitations": []
}
```

`schemas/analysis.schema.json` is normative. `thresholds` echoes every setting
that influenced a number, so a third party can reproduce the run.

## Findings

```json
{
  "finding_id": "F-001",
  "claim_type": "calculated",
  "statement": "...",
  "evidence": [],
  "assumptions": [],
  "limitations": [],
  "recommended_action": [],
  "evidence_quality": "medium",
  "evidence_assessment": {
    "factors": { "routed_orders": 15, "route_coverage_percent": 100.0 },
    "site_verified": false,
    "rationale": "15 routed orders at 100.0% coverage on a 'scan_confirmed' sequence.",
    "caps_applied": []
  }
}
```

`claim_type` is `observed`, `calculated`, `simulated` or `hypothesis`.
`evidence_quality` is `high`, `medium`, `low` or `insufficient` — a label, never
a probability. `evidence_assessment` carries the factors that produced it, so the
label can be recomputed by hand; `evidence_rules.md` documents one ladder per
finding family. `evidence_quality` is kept for compatibility with 0.1.0
consumers.

`analysis.thresholds` echoes `sequence_basis`, `distance_basis` and the evidence
thresholds in force, so a report always carries the rules behind its own numbers.

## Issues

```json
{
  "code": "DISCONNECTED_ROUTE",
  "severity": "error",
  "field": "layout.edges",
  "message": "No pedestrian route exists between N04 and N09.",
  "affected_records": ["PICK-0018"]
}
```

## Status and exit codes

| Status | Exit code | Meaning |
| --- | --- | --- |
| `ok` | 0 | every order reconstructed, no error |
| `partial` | 2 | some orders analysable, some excluded |
| `insufficient_evidence` | 2 | nothing could be reconstructed |
| `data_error` | 1 | a structural error blocks the analysis |

## SVG

Both drawings are produced with the standard library, carry `<title>` and
`<desc>`, reference no external resource, and use a sober palette that reads on a
light background: `#228B22` for flow, `#111111` for ink, neutral greys for the
rest. The palette is deliberately minimal and configurable; the engine does not
embed a full brand identity.

`flow-map.svg` shows nodes, aisle segments, zones, locations, depots, modelled
traversal frequency per segment, and a legend. Line thickness and opacity encode
the traversal count. Route start, route end, packing, shipping and receiving each
get a distinct marker, and an arrowhead is drawn **only** on genuinely one-way
segments, so an arrow always means something.

`spaghetti.svg` can show one specific order, the N longest orders, or the
aggregated flow. The number of drawn routes is capped so the picture stays
readable, and the caption inside the drawing says how many routes of how many are
shown.

## HTML report

One static file. No external resource, no web font, no script required for the
content. It prints, it is responsive, it embeds both SVGs, and a fictional
dataset is clearly labelled *Fictional example*.

Sections, in order:

1. Summary
2. Data quality
3. Flow metrics
4. ABC / XYZ classification
5. Leading locations
6. Co-pick affinities
7. Visualisation
8. Simulated recommendations
9. Assumptions
10. Limitations
11. Field validation actions

followed by the findings register and the evidence rules that were applied.
