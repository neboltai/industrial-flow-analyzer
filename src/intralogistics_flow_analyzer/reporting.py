"""Analysis pipeline and report generation (JSON, CSV, Markdown, HTML, SVG).

The HTML report is a single static file: no external stylesheet, no web font, no
script required for the content, no network access. It prints, it is responsive,
and it repeats the evidence rules next to the numbers they govern.
"""

from __future__ import annotations

import html as html_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import metrics as metrics_module
from .affinity import affinity_thresholds, compute_affinities
from .classification import abc_classify, combined_classes, xyz_classify
from .evidence import thresholds_for
from .findings import build_findings
from .graph import Graph, build_graph
from .loaders import write_csv, write_json, write_text
from .models import Dataset, ENGINE_VERSION, SCHEMA_VERSION
from .routing import RoutingResult, build_routes
from .slotting import SlottingResult, recommend
from .svg import render_flow_map, render_spaghetti
from .validation import ValidationReport, validate

ENGINE_LIMITATIONS = [
    "Schema 1.0 evaluates swaps between exactly two pick locations. It does not create, "
    "split or merge locations and it does not search for a global optimum.",
    "Replenishment travel, put-away travel and the one-off effort of relocating stock are "
    "outside the model.",
    "Congestion, queueing, shift patterns and equipment availability are not modelled.",
    "Reserve locations are represented but are not optimised.",
    "Pick sequencing and batching strategies are taken as given and are never re-optimised.",
    "Distances are modelled on the declared aisle graph as the shortest permitted path between consecutive pick access nodes; they are never a measurement of the path actually walked.",
    "The analyser produces no individual performance measure of any kind.",
]

EVIDENCE_RULES = [
    "A high pick frequency is not evidence of a badly placed item.",
    "A long route is not evidence of avoidable travel.",
    "A heavily traversed aisle segment in the model is not evidence of congestion.",
    "Co-occurrence of two items is not evidence that they belong next to each other.",
    "A simulated reduction is not a realised saving.",
    "Relocation effort is reported separately from any potential gain, and is not estimated here.",
    "Replenishment distance is kept separate from picking distance.",
    "No slotting change is proposed while a critical constraint value is missing.",
    "Every scenario names the historical orders it replayed.",
    "Every conclusion traces back to a record or a calculation in this report.",
]

#: Report tables are English. The German terms are kept only where the four claim
#: types are introduced as a glossary, never mixed into a data column.
CLAIM_LABELS = {
    "observed": "Observed",
    "calculated": "Calculated",
    "simulated": "Simulated",
    "hypothesis": "Hypothesis",
}

CLAIM_GLOSSARY = (
    ("observed", "Observed", "Fakt", "counted directly in the input records"),
    ("calculated", "Calculated", "Berechnung", "derived from the graph and the input records"),
    (
        "simulated",
        "Simulated",
        "Simulation",
        "produced by replaying history against a modified slotting",
    ),
    (
        "hypothesis",
        "Hypothesis",
        "Hypothese",
        "a question raised by the data, to be verified on site",
    ),
)

MISSING_EVIDENCE_LABEL = "Insufficient evidence"


@dataclass
class AnalysisResult:
    dataset: Dataset
    graph: Graph
    validation: ValidationReport
    routing: RoutingResult
    analysis: Dict[str, Any]
    slotting: SlottingResult
    location_rows: List[Dict[str, Any]] = field(default_factory=list)
    sku_rows: List[Dict[str, Any]] = field(default_factory=list)
    edge_rows: List[Dict[str, Any]] = field(default_factory=list)


def _thresholds(dataset: Dataset, slotting: SlottingResult) -> Dict[str, Any]:
    config = dataset.config
    thresholds = {
        "abc_basis": config.get("abc_basis"),
        "abc_thresholds": config.get("abc_thresholds"),
        "xyz_time_bucket": config.get("xyz_time_bucket"),
        "xyz_minimum_buckets": config.get("xyz_minimum_buckets"),
        "xyz_thresholds": config.get("xyz_thresholds"),
        "default_mode": config.get("default_mode"),
        "route_start_node": config.get("route_start_node"),
        "route_end_node": config.get("route_end_node"),
        "percentile_method": "linear interpolation between closest ranks",
        "sequence_basis": dataset.meta.sequence_basis,
        "distance_basis": dataset.meta.distance_basis,
        "evidence": thresholds_for(dataset.config),
    }
    thresholds.update(affinity_thresholds(dataset))
    thresholds.update(slotting.thresholds)
    return thresholds


def analyse(
    dataset: Dataset,
    top_recommendations: Optional[int] = None,
    graph: Optional[Graph] = None,
) -> AnalysisResult:
    """Run the full deterministic pipeline and assemble ``analysis.json``."""
    graph = graph or build_graph(dataset.nodes, dataset.edges)
    report = validate(dataset, graph)
    routing = build_routes(dataset, graph)

    flow_metrics = metrics_module.compute_metrics(dataset, routing, graph)
    quality = metrics_module.data_quality(dataset, routing)
    location_rows = metrics_module.location_metrics(dataset, routing, graph)
    sku_rows = metrics_module.sku_metrics(dataset, routing, graph)
    edge_rows = metrics_module.edge_flows(dataset, routing)
    zone_rows = metrics_module.zone_flows(dataset, routing)

    abc_rows = abc_classify(dataset)
    xyz_rows, xyz_marker = xyz_classify(dataset)
    affinity_rows = compute_affinities(dataset)

    if report.status == "data_error":
        slotting = SlottingResult(thresholds={"skipped": "dataset status is data_error"})
    else:
        slotting = recommend(dataset, graph, routing, top_recommendations)

    findings = build_findings(
        meta=dataset.meta.to_dict(),
        config=dataset.config,
        metrics=flow_metrics,
        data_quality=quality,
        location_rows=location_rows,
        sku_rows=sku_rows,
        edge_rows=edge_rows,
        abc_rows=abc_rows,
        xyz_rows=xyz_rows,
        xyz_marker=xyz_marker,
        affinity_rows=affinity_rows,
        recommendations=slotting.recommendations,
        rejected_candidates=slotting.rejected_candidates,
    )

    analysis: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "status": report.status,
        "meta": dataset.meta.to_dict(),
        "issues": [i.to_dict() for i in report.issues],
        "data_quality": quality,
        "metrics": flow_metrics,
        "edge_flows": edge_rows,
        "zone_flows": zone_rows,
        "location_metrics": location_rows,
        "sku_metrics": sku_rows,
        "abc": abc_rows,
        "xyz": xyz_rows,
        "xyz_marker": xyz_marker,
        "combined_classes": combined_classes(abc_rows, xyz_rows),
        "affinities": affinity_rows,
        "recommendations": slotting.recommendations,
        "rejected_candidates": slotting.rejected_candidates,
        "slotting_method": slotting.to_dict()["method"],
        "top_orders_by_distance": metrics_module.top_orders_by_distance(routing, 10),
        "top_locations_by_distance": metrics_module.top_locations_by_distance(location_rows, 10),
        "routes": [r.to_dict() for r in routing.routes],
        "findings": findings,
        "thresholds": _thresholds(dataset, slotting),
        "limitations": list(ENGINE_LIMITATIONS),
        "evidence_rules": list(EVIDENCE_RULES),
    }
    return AnalysisResult(
        dataset=dataset,
        graph=graph,
        validation=report,
        routing=routing,
        analysis=analysis,
        slotting=slotting,
        location_rows=location_rows,
        sku_rows=sku_rows,
        edge_rows=edge_rows,
    )


# --------------------------------------------------------------------- CSV

RECOMMENDATION_COLUMNS = [
    "recommendation_id",
    "type",
    "sku_a",
    "from_a",
    "to_a",
    "sku_b",
    "from_b",
    "to_b",
    "baseline_distance_m",
    "simulated_distance_m",
    "estimated_reduction_m",
    "estimated_reduction_percent",
    "affected_orders",
    "check_volume",
    "check_weight",
    "check_unit_weight",
    "check_temperature",
    "check_hazard",
    "check_handling_mode",
    "check_zone",
    "check_level",
    "check_fixed",
    "measure",
]


def recommendation_rows(recommendations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for recommendation in recommendations:
        row = {key: recommendation.get(key) for key in RECOMMENDATION_COLUMNS}
        for name, value in (recommendation.get("constraint_checks") or {}).items():
            row[f"check_{name}"] = value
        rows.append(row)
    return rows


def write_recommendations_csv(path: Path, recommendations: Sequence[Dict[str, Any]]) -> Path:
    return write_csv(path, RECOMMENDATION_COLUMNS, recommendation_rows(recommendations))


# ---------------------------------------------------------------- Markdown


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}{suffix}".replace(",", " ")
    return f"{value}{suffix}"


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join([" --- "] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines) + "\n"


def render_markdown(result: AnalysisResult) -> str:
    analysis = result.analysis
    dataset = result.dataset
    meta = analysis["meta"]
    metrics = analysis["metrics"]
    quality = analysis["data_quality"]
    fictional_banner = (
        "> **Fictional example.** This report was produced from a fictional dataset "
        "shipped with the Community Edition. It contains no real company, site or person.\n"
        if meta.get("fictional")
        else ""
    )

    parts: List[str] = []
    parts.append(f"# Intralogistics flow analysis — {meta.get('dataset_id')}\n")
    parts.append(fictional_banner)
    parts.append(
        f"Engine `{analysis['engine_version']}` · schema `{analysis['schema_version']}` · "
        f"status **{analysis['status']}**\n"
    )

    parts.append("\n## 1. Summary\n")
    parts.append(
        f"- Period: `{meta.get('period_start')}` to `{meta.get('period_end')}` "
        f"({meta.get('time_zone') or 'time zone not declared'})\n"
        f"- Orders reconstructed: **{quality['orders_routed']} / {quality['orders_total']}** "
        f"({_fmt(quality['route_coverage_percent'], ' %')})\n"
        f"- Modelled picking travel: **{_fmt(metrics.get('total_distance_m'), ' m')}**\n"
        f"- Median per order: {_fmt(metrics.get('median_distance_per_order_m'), ' m')} · "
        f"p90: {_fmt(metrics.get('p90_distance_per_order_m'), ' m')}\n"
        f"- Constraint-safe swaps proposed: **{len(analysis['recommendations'])}**\n"
    )
    if analysis["recommendations"]:
        total_reduction = sum(
            r.get("estimated_reduction_m") or 0.0 for r in analysis["recommendations"]
        )
        parts.append(
            f"- Highest single {analysis['recommendations'][0]['measure']}: "
            f"{_fmt(analysis['recommendations'][0]['estimated_reduction_m'], ' m')} "
            f"({_fmt(analysis['recommendations'][0]['estimated_reduction_percent'], ' %')})\n"
            f"- Sum of individually simulated reductions: {_fmt(total_reduction, ' m')} "
            "(each swap was simulated on its own; applying several together has not been "
            "simulated)\n"
        )

    parts.append("\n### Provenance of the figures\n")
    parts.append(
        f"- `sequence_basis`: **{meta.get('sequence_basis')}** — "
        f"{dataset.meta.sequence_basis_label()}\n"
        f"- `distance_basis`: **{meta.get('distance_basis')}** — "
        f"{dataset.meta.distance_basis_label()}\n"
        "\nEvery distance in this report is modelled, not measured. A pick sequence may be "
        "planned by the system, confirmed by scan, observed from movement records, or of "
        "undeclared provenance; only `scan_confirmed` and `observed` support a statement about "
        "the order actually walked.\n"
    )

    parts.append("\n## 2. Data quality\n")
    parts.append(
        _md_table(
            ["Measure", "Value"],
            [[key.replace("_", " "), value] for key, value in quality.items()],
        )
    )
    issues = analysis["issues"]
    if issues:
        parts.append("\n### Issues\n")
        parts.append(
            _md_table(
                ["Code", "Severity", "Field", "Message", "Affected"],
                [
                    [
                        i["code"],
                        i["severity"],
                        i["field"],
                        i["message"],
                        ", ".join(i["affected_records"][:5]) or "-",
                    ]
                    for i in issues
                ],
            )
        )
    else:
        parts.append("\nNo validation issue was raised.\n")

    parts.append("\n## 3. Flow metrics\n")
    parts.append(
        _md_table(
            ["Metric", "Value"],
            [[key.replace("_", " "), _fmt(value)] for key, value in metrics.items()],
        )
    )
    parts.append("\n### Most-traversed aisle segments (modelled)\n")
    parts.append(
        _md_table(
            ["Edge", "From", "To", "Traversals", "Share %", "Travelled m"],
            [
                [
                    row["edge_id"],
                    row["from_node"],
                    row["to_node"],
                    row["traversals"],
                    _fmt(row["traversal_share_percent"]),
                    _fmt(row["travelled_distance_m"]),
                ]
                for row in analysis["edge_flows"][:10]
            ],
        )
    )
    parts.append("\n### Longest modelled order routes\n")
    parts.append(
        _md_table(
            ["Order", "Distance m", "Lines", "Zone transitions", "Mode"],
            [
                [
                    row["order_id"],
                    _fmt(row["total_distance_m"]),
                    row["number_of_lines"],
                    row["zone_transitions"],
                    row["mode"],
                ]
                for row in analysis["top_orders_by_distance"]
            ],
        )
    )

    parts.append("\n## 4. ABC / XYZ classification\n")
    parts.append(
        f"ABC basis `{analysis['thresholds']['abc_basis']}`, thresholds "
        f"`{analysis['thresholds']['abc_thresholds']}`. An item that crosses a threshold "
        "belongs to the class it completes.\n\n"
    )
    parts.append(
        _md_table(
            ["SKU", "Rank", "Basis value", "Share %", "Cumulative %", "ABC", "XYZ", "Combined"],
            [
                [
                    row["sku"],
                    row["rank"],
                    _fmt(row["basis_value"]),
                    _fmt(row["share_percent"]),
                    _fmt(row["cumulative_share_percent"]),
                    row["abc_class"],
                    next(
                        (x.get("xyz_class") or "-" for x in analysis["xyz"] if x["sku"] == row["sku"]),
                        "-",
                    ),
                    analysis["combined_classes"].get(row["sku"], row["abc_class"]),
                ]
                for row in analysis["abc"]
            ],
        )
    )
    if analysis["xyz_marker"]:
        parts.append(
            f"\n**{MISSING_EVIDENCE_LABEL}:** `{analysis['xyz_marker']}` — the observed period "
            "provides fewer time buckets than `xyz_minimum_buckets`. No SKU was labelled X, Y "
            "or Z, and the absence of a class must not be read as a class.\n"
        )
    else:
        parts.append(
            "\nXYZ uses the coefficient of variation of per-bucket pick lines "
            f"(bucket `{analysis['thresholds']['xyz_time_bucket']}`, thresholds "
            f"`{analysis['thresholds'].get('xyz_thresholds')}`).\n"
        )

    parts.append("\n## 5. Leading locations\n")
    parts.append(
        _md_table(
            ["Location", "Zone", "Visits", "Visit %", "Attributable modelled m", "Distance % ", "From start m"],
            [
                [
                    row["location_id"],
                    row["zone_id"] or "-",
                    row["visits"],
                    _fmt(row["visit_share_percent"]),
                    _fmt(row["attributable_distance_m"]),
                    _fmt(row["distance_share_percent"]),
                    _fmt(row["distance_from_start_node_m"]),
                ]
                for row in analysis["top_locations_by_distance"]
            ],
        )
    )
    parts.append(
        "\nDistance attribution convention: the leg arriving at a location is charged to that "
        "location; the return leg is charged to the last location of the order. This is an "
        "accounting convention, not a measurement.\n"
    )

    parts.append("\n## 6. Co-pick affinities\n")
    if analysis["affinities"]:
        parts.append(
            _md_table(
                ["SKU A", "SKU B", "Co-picks", "Support %", "Conf. A→B", "Conf. B→A", "Lift"],
                [
                    [
                        row["sku_a"],
                        row["sku_b"],
                        row["co_pick_count"],
                        _fmt(row["support_percent"]),
                        _fmt(row["confidence_a_to_b"]),
                        _fmt(row["confidence_b_to_a"]),
                        _fmt(row["lift"]),
                    ]
                    for row in analysis["affinities"][:15]
                ],
            )
        )
        parts.append(
            "\nCo-occurrence describes how often two items appear in the same order. It does "
            "not establish causality and does not by itself justify relocating either item.\n"
        )
    else:
        parts.append("No item pair reached the configured affinity thresholds.\n")

    parts.append("\n## 7. Visualisation\n")
    parts.append(
        "- `flow-map.svg` — layout with modelled traversal intensity per aisle segment, route start and end, and arrows on one-way segments only\n"
        "- `spaghetti.svg` — the longest modelled order routes\n"
    )

    parts.append("\n## 8. Simulated recommendations\n")
    if analysis["recommendations"]:
        parts.append(
            _md_table(
                [
                    "ID",
                    "SKU A",
                    "A: from → to",
                    "SKU B",
                    "B: from → to",
                    "Baseline m",
                    "Simulated m",
                    "Reduction m",
                    "Reduction %",
                    "Affected orders",
                ],
                [
                    [
                        row["recommendation_id"],
                        row["sku_a"],
                        f"{row['from_a']} → {row['to_a']}",
                        row["sku_b"],
                        f"{row['from_b']} → {row['to_b']}",
                        _fmt(row["baseline_distance_m"]),
                        _fmt(row["simulated_distance_m"]),
                        _fmt(row["estimated_reduction_m"]),
                        _fmt(row["estimated_reduction_percent"]),
                        row["affected_orders"],
                    ]
                    for row in analysis["recommendations"]
                ],
            )
        )
        parts.append("\n#### Constraint checks\n")
        parts.append(
            _md_table(
                ["ID"] + [name for name in (analysis["recommendations"][0]["constraint_checks"])],
                [
                    [row["recommendation_id"]] + list(row["constraint_checks"].values())
                    for row in analysis["recommendations"]
                ],
            )
        )
    else:
        parts.append(
            "No candidate swap cleared every hard constraint and the configured minimum "
            "improvement threshold.\n"
        )

    rejected = analysis["rejected_candidates"]
    if rejected:
        parts.append("\n### Rejected candidates\n")
        parts.append(
            _md_table(
                ["SKU A", "SKU B", "Status", "Blocking checks"],
                [
                    [
                        row["sku_a"],
                        row["sku_b"],
                        row.get("status", "-"),
                        ", ".join(row.get("rejected_because") or []) or "-",
                    ]
                    for row in rejected[:25]
                ],
            )
        )

    parts.append("\n## 9. Assumptions\n")
    assumptions = sorted(
        {
            assumption
            for finding in analysis["findings"]
            for assumption in finding["assumptions"]
        }
    )
    parts.append("".join(f"- {a}\n" for a in assumptions) or "- None recorded.\n")

    parts.append("\n## 10. Limitations\n")
    parts.append("".join(f"- {limitation}\n" for limitation in analysis["limitations"]))

    parts.append("\n## 11. Field validation actions\n")
    actions = []
    for finding in analysis["findings"]:
        for action in finding["recommended_action"]:
            if action not in actions:
                actions.append(action)
    parts.append(
        "".join(f"- {action}\n" for action in actions)
        or "- No field validation action was derived.\n"
    )

    parts.append("\n## Findings register\n")
    parts.append(
        "Claim types: "
        + " · ".join(f"**{en}** ({de})" for _, en, de, _ in CLAIM_GLOSSARY)
        + ". Evidence quality is a documented label, never a probability; each finding "
        "carries the factors that produced it.\n\n"
    )
    parts.append(
        _md_table(
            ["ID", "Claim type", "Evidence quality", "Why that level", "Statement"],
            [
                [
                    finding["finding_id"],
                    CLAIM_LABELS.get(finding["claim_type"], finding["claim_type"]),
                    finding["evidence_quality"],
                    finding["evidence_assessment"]["rationale"],
                    finding["statement"],
                ]
                for finding in analysis["findings"]
            ],
        )
    )

    parts.append("\n## Evidence rules applied\n")
    parts.append("".join(f"- {rule}\n" for rule in analysis["evidence_rules"]))
    parts.append(
        "\n---\n\nNo individual worker data is processed, stored or reported. The unit of "
        "analysis is the order, the route, the item, the location and the zone.\n"
    )
    return "".join(parts)


# -------------------------------------------------------------------- HTML

_CSS = """
:root {
  --ink: #111111;
  --muted: #5d5d5d;
  --accent: #228B22;
  --line: #d8d8d8;
  --surface: #ffffff;
  --panel: #f6f7f6;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 0 1rem 4rem;
  background: var(--surface);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  font-size: 16px;
  line-height: 1.55;
}
main { max-width: 1040px; margin: 0 auto; }
header.report {
  border-bottom: 3px solid var(--accent);
  padding: 2rem 0 1rem;
  margin-bottom: 1.5rem;
}
h1 { font-size: 1.7rem; margin: 0 0 .4rem; }
h2 { font-size: 1.25rem; margin: 2.2rem 0 .6rem; border-bottom: 1px solid var(--line);
     padding-bottom: .3rem; }
h3 { font-size: 1.05rem; margin: 1.4rem 0 .4rem; }
p, li { margin: .35rem 0; }
.subtitle { color: var(--muted); font-size: .95rem; }
.banner {
  background: var(--panel);
  border-left: 4px solid var(--accent);
  padding: .8rem 1rem;
  margin: 1rem 0;
  font-size: .95rem;
}
.badge {
  display: inline-block; padding: .1rem .5rem; border: 1px solid var(--line);
  border-radius: 999px; font-size: .78rem; color: var(--muted); margin-right: .3rem;
}
.badge.ok { border-color: var(--accent); color: var(--accent); }
.kpi-grid {
  display: grid; gap: .8rem; margin: 1rem 0;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
}
.kpi { border: 1px solid var(--line); border-radius: 6px; padding: .8rem; background: var(--panel); }
.kpi .value { font-size: 1.35rem; font-weight: 600; }
.kpi .label { font-size: .8rem; color: var(--muted); text-transform: uppercase;
              letter-spacing: .04em; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .88rem; margin: .6rem 0 1rem; }
th, td { border: 1px solid var(--line); padding: .35rem .5rem; text-align: left;
         vertical-align: top; }
th { background: var(--panel); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pass { color: var(--accent); font-weight: 600; }
.fail { color: #8B1A1A; font-weight: 600; }
.insufficient { color: var(--muted); font-weight: 600; }
figure { margin: 1rem 0; border: 1px solid var(--line); border-radius: 6px; padding: .6rem;
         background: var(--surface); }
figure svg { width: 100%; height: auto; display: block; }
figcaption { font-size: .85rem; color: var(--muted); margin-top: .5rem; }
.claim { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); }
.finding { border-left: 3px solid var(--line); padding: .3rem 0 .3rem .8rem; margin: .8rem 0; }
.finding.simulated { border-left-color: var(--accent); }
.finding.hypothesis { border-left-color: #b0b0b0; border-left-style: dashed; }
.finding ul { margin: .3rem 0 .3rem 1.1rem; padding: 0; }
.assessment { font-size: .85rem; color: var(--muted); }
footer.report { margin-top: 3rem; border-top: 1px solid var(--line); padding-top: 1rem;
                color: var(--muted); font-size: .85rem; }
@media print {
  body { font-size: 11pt; padding: 0; }
  h2 { page-break-after: avoid; }
  figure, table { page-break-inside: avoid; }
  .kpi { background: none; }
}
@media (max-width: 640px) {
  body { font-size: 15px; }
  h1 { font-size: 1.35rem; }
  table { font-size: .8rem; }
}
"""


def _esc(value: Any) -> str:
    return html_module.escape("" if value is None else str(value), quote=True)


def _html_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    numeric_columns: Sequence[int] = (),
    check_columns: Sequence[int] = (),
) -> str:
    if not rows:
        return "<p><em>No rows.</em></p>"
    head = "".join(
        f'<th class="{"num" if index in numeric_columns else ""}">{_esc(header)}</th>'
        for index, header in enumerate(headers)
    )
    body_rows: List[str] = []
    for row in rows:
        cells: List[str] = []
        for index, cell in enumerate(row):
            classes: List[str] = []
            if index in numeric_columns:
                classes.append("num")
            if index in check_columns and str(cell) in ("pass", "fail", "insufficient_data"):
                classes.append(
                    "pass" if cell == "pass" else ("fail" if cell == "fail" else "insufficient")
                )
            attribute = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<td{attribute}>{_esc(cell)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="table-wrap"><table><thead><tr>'
        + head
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


def _strip_svg_prolog(svg: str) -> str:
    return svg.split("?>", 1)[1].strip() if svg.lstrip().startswith("<?xml") else svg.strip()


def render_html(result: AnalysisResult, flow_map_svg: str, spaghetti_svg: str) -> str:
    analysis = result.analysis
    meta = analysis["meta"]
    metrics = analysis["metrics"]
    quality = analysis["data_quality"]

    banner = ""
    if meta.get("fictional"):
        banner = (
            '<div class="banner"><strong>Fictional example.</strong> This report was produced '
            "from a fictional dataset shipped with the Community Edition. It contains no real "
            "company, site or person.</div>"
        )

    kpis = [
        ("Modelled travel", _fmt(metrics.get("total_distance_m"), " m")),
        ("Orders reconstructed", f"{quality['orders_routed']} / {quality['orders_total']}"),
        ("Median per order", _fmt(metrics.get("median_distance_per_order_m"), " m")),
        ("p90 per order", _fmt(metrics.get("p90_distance_per_order_m"), " m")),
        ("Distance per pick line", _fmt(metrics.get("average_distance_per_pick_m"), " m")),
        ("Constraint-safe swaps", str(len(analysis["recommendations"]))),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="label">{_esc(label)}</div>'
        f'<div class="value">{_esc(value)}</div></div>'
        for label, value in kpis
    )

    sections: List[str] = []

    sections.append("<h2>1. Summary</h2>")
    sections.append(f'<div class="kpi-grid">{kpi_html}</div>')
    sections.append(
        "<p>Period <code>{start}</code> to <code>{end}</code> ({tz}). Route coverage "
        "{coverage} of orders. Distances are reconstructed from the declared aisle graph "
        "using the historical pick sequence.</p>".format(
            start=_esc(meta.get("period_start")),
            end=_esc(meta.get("period_end")),
            tz=_esc(meta.get("time_zone") or "time zone not declared"),
            coverage=_esc(_fmt(quality["route_coverage_percent"], " %")),
        )
    )

    sections.append(
        '<div class="banner"><strong>Provenance of the figures.</strong> '
        "<code>sequence_basis</code>: <strong>{sequence}</strong> — {sequence_label}. "
        "<code>distance_basis</code>: <strong>{distance}</strong> — {distance_label}. "
        "Every distance in this report is modelled, not measured; only a "
        "<code>scan_confirmed</code> or <code>observed</code> sequence supports a statement "
        "about the order actually walked.</div>".format(
            sequence=_esc(meta.get("sequence_basis")),
            sequence_label=_esc(result.dataset.meta.sequence_basis_label()),
            distance=_esc(meta.get("distance_basis")),
            distance_label=_esc(result.dataset.meta.distance_basis_label()),
        )
    )

    sections.append("<h2>2. Data quality</h2>")
    sections.append(
        _html_table(
            ["Measure", "Value"],
            [[key.replace("_", " "), value] for key, value in quality.items()],
            numeric_columns=(1,),
        )
    )
    if analysis["issues"]:
        sections.append("<h3>Validation issues</h3>")
        sections.append(
            _html_table(
                ["Code", "Severity", "Field", "Message", "Affected records"],
                [
                    [
                        i["code"],
                        i["severity"],
                        i["field"],
                        i["message"],
                        ", ".join(i["affected_records"][:6]) or "-",
                    ]
                    for i in analysis["issues"]
                ],
            )
        )
    else:
        sections.append("<p>No validation issue was raised.</p>")

    sections.append("<h2>3. Flow metrics</h2>")
    sections.append(
        _html_table(
            ["Metric", "Value"],
            [[key.replace("_", " "), _fmt(value)] for key, value in metrics.items()],
            numeric_columns=(1,),
        )
    )
    sections.append("<h3>Most-traversed aisle segments (modelled)</h3>")
    sections.append(
        _html_table(
            ["Edge", "From", "To", "Traversals", "Share %", "Travelled m"],
            [
                [
                    row["edge_id"],
                    row["from_node"],
                    row["to_node"],
                    row["traversals"],
                    _fmt(row["traversal_share_percent"]),
                    _fmt(row["travelled_distance_m"]),
                ]
                for row in analysis["edge_flows"][:10]
            ],
            numeric_columns=(3, 4, 5),
        )
    )
    sections.append("<h3>Longest modelled order routes</h3>")
    sections.append(
        _html_table(
            ["Order", "Distance m", "Lines", "Zone transitions", "Mode"],
            [
                [
                    row["order_id"],
                    _fmt(row["total_distance_m"]),
                    row["number_of_lines"],
                    row["zone_transitions"],
                    row["mode"],
                ]
                for row in analysis["top_orders_by_distance"]
            ],
            numeric_columns=(1, 2, 3),
        )
    )

    sections.append("<h2>4. ABC / XYZ classification</h2>")
    sections.append(
        "<p>ABC basis <code>{basis}</code>, thresholds <code>{thresholds}</code>. "
        "An item that crosses a threshold belongs to the class it completes.</p>".format(
            basis=_esc(analysis["thresholds"]["abc_basis"]),
            thresholds=_esc(analysis["thresholds"]["abc_thresholds"]),
        )
    )
    sections.append(
        _html_table(
            ["SKU", "Rank", "Basis value", "Share %", "Cumulative %", "ABC", "XYZ", "Combined"],
            [
                [
                    row["sku"],
                    row["rank"],
                    _fmt(row["basis_value"]),
                    _fmt(row["share_percent"]),
                    _fmt(row["cumulative_share_percent"]),
                    row["abc_class"],
                    next(
                        (x.get("xyz_class") or "-" for x in analysis["xyz"] if x["sku"] == row["sku"]),
                        "-",
                    ),
                    analysis["combined_classes"].get(row["sku"], row["abc_class"]),
                ]
                for row in analysis["abc"]
            ],
            numeric_columns=(1, 2, 3, 4),
        )
    )
    if analysis["xyz_marker"]:
        sections.append(
            '<div class="banner"><strong>{label}</strong> <code>{marker}</code> — the observed '
            "period provides fewer time buckets than the configured minimum. No SKU was "
            "labelled X, Y or Z. The absence of a class must not be read as a class.</div>".format(
                label=_esc(MISSING_EVIDENCE_LABEL), marker=_esc(analysis["xyz_marker"])
            )
        )

    sections.append("<h2>5. Leading locations</h2>")
    sections.append(
        _html_table(
            ["Location", "Zone", "Visits", "Visit %", "Attributable modelled m", "Distance %", "From start m"],
            [
                [
                    row["location_id"],
                    row["zone_id"] or "-",
                    row["visits"],
                    _fmt(row["visit_share_percent"]),
                    _fmt(row["attributable_distance_m"]),
                    _fmt(row["distance_share_percent"]),
                    _fmt(row["distance_from_start_node_m"]),
                ]
                for row in analysis["top_locations_by_distance"]
            ],
            numeric_columns=(2, 3, 4, 5, 6),
        )
    )
    sections.append(
        "<p>Distance attribution convention: the leg arriving at a location is charged to that "
        "location; the return leg is charged to the last location of the order. This is an "
        "accounting convention, not a measurement.</p>"
    )

    sections.append("<h2>6. Co-pick affinities</h2>")
    if analysis["affinities"]:
        sections.append(
            _html_table(
                ["SKU A", "SKU B", "Co-picks", "Support %", "Conf. A\u2192B", "Conf. B\u2192A", "Lift"],
                [
                    [
                        row["sku_a"],
                        row["sku_b"],
                        row["co_pick_count"],
                        _fmt(row["support_percent"]),
                        _fmt(row["confidence_a_to_b"]),
                        _fmt(row["confidence_b_to_a"]),
                        _fmt(row["lift"]),
                    ]
                    for row in analysis["affinities"][:15]
                ],
                numeric_columns=(2, 3, 4, 5, 6),
            )
        )
        sections.append(
            "<p>Co-occurrence describes how often two items appear in the same order. It does "
            "not establish causality and does not by itself justify relocating either item.</p>"
        )
    else:
        sections.append("<p>No item pair reached the configured affinity thresholds.</p>")

    sections.append("<h2>7. Visualisation</h2>")
    sections.append(
        "<figure>"
        + _strip_svg_prolog(flow_map_svg)
        + "<figcaption>Flow map. Line thickness and opacity encode how often each aisle "
        "segment was traversed in the reconstructed routes.</figcaption></figure>"
    )
    sections.append(
        "<figure>"
        + _strip_svg_prolog(spaghetti_svg)
        + "<figcaption>Spaghetti diagram of the longest reconstructed routes. The number of "
        "drawn routes is capped so the picture stays readable.</figcaption></figure>"
    )

    sections.append("<h2>8. Simulated recommendations</h2>")
    if analysis["recommendations"]:
        sections.append(
            _html_table(
                [
                    "ID",
                    "SKU A",
                    "A: from \u2192 to",
                    "SKU B",
                    "B: from \u2192 to",
                    "Baseline m",
                    "Simulated m",
                    "Reduction m",
                    "Reduction %",
                    "Affected orders",
                ],
                [
                    [
                        row["recommendation_id"],
                        row["sku_a"],
                        f"{row['from_a']} → {row['to_a']}",
                        row["sku_b"],
                        f"{row['from_b']} → {row['to_b']}",
                        _fmt(row["baseline_distance_m"]),
                        _fmt(row["simulated_distance_m"]),
                        _fmt(row["estimated_reduction_m"]),
                        _fmt(row["estimated_reduction_percent"]),
                        row["affected_orders"],
                    ]
                    for row in analysis["recommendations"]
                ],
                numeric_columns=(5, 6, 7, 8, 9),
            )
        )
        check_names = list(analysis["recommendations"][0]["constraint_checks"])
        sections.append("<h3>Constraint checks</h3>")
        sections.append(
            _html_table(
                ["ID"] + check_names,
                [
                    [row["recommendation_id"]] + list(row["constraint_checks"].values())
                    for row in analysis["recommendations"]
                ],
                check_columns=tuple(range(1, len(check_names) + 1)),
            )
        )
        sections.append(
            "<p>Every figure above is an <strong>estimated pick-distance reduction</strong> "
            "obtained by replaying the historical orders with an unchanged pick sequence. "
            "It is not a realised saving, and it excludes replenishment travel and the effort "
            "of physically relocating stock.</p>"
        )
    else:
        sections.append(
            "<p>No candidate swap cleared every hard constraint and the configured minimum "
            "improvement threshold.</p>"
        )

    if analysis["rejected_candidates"]:
        sections.append("<h3>Rejected candidates</h3>")
        sections.append(
            _html_table(
                ["SKU A", "SKU B", "Status", "Blocking checks"],
                [
                    [
                        row["sku_a"],
                        row["sku_b"],
                        row.get("status", "-"),
                        ", ".join(row.get("rejected_because") or []) or "-",
                    ]
                    for row in analysis["rejected_candidates"][:25]
                ],
            )
        )

    assumptions = sorted(
        {a for finding in analysis["findings"] for a in finding["assumptions"]}
    )
    sections.append("<h2>9. Assumptions</h2>")
    sections.append(
        "<ul>" + "".join(f"<li>{_esc(a)}</li>" for a in assumptions) + "</ul>"
        if assumptions
        else "<p>None recorded.</p>"
    )

    sections.append("<h2>10. Limitations</h2>")
    sections.append(
        "<ul>" + "".join(f"<li>{_esc(l)}</li>" for l in analysis["limitations"]) + "</ul>"
    )

    actions: List[str] = []
    for finding in analysis["findings"]:
        for action in finding["recommended_action"]:
            if action not in actions:
                actions.append(action)
    sections.append("<h2>11. Field validation actions</h2>")
    sections.append(
        "<ul>" + "".join(f"<li>{_esc(a)}</li>" for a in actions) + "</ul>"
        if actions
        else "<p>No field validation action was derived.</p>"
    )

    sections.append("<h2>Findings register</h2>")
    sections.append(
        "<p>Claim types: "
        + " · ".join(
            f"<strong>{_esc(en)}</strong> ({_esc(de)}) — {_esc(gloss)}"
            for _, en, de, gloss in CLAIM_GLOSSARY
        )
        + ". Evidence quality is a documented label, never a probability.</p>"
    )
    for finding in analysis["findings"]:
        blocks = [
            f'<div class="claim">{_esc(finding["finding_id"])} · '
            f'{_esc(CLAIM_LABELS.get(finding["claim_type"], finding["claim_type"]))} · '
            f'evidence quality {_esc(finding["evidence_quality"])}</div>',
            f'<p>{_esc(finding["statement"])}</p>',
            '<p class="assessment"><strong>Why that level:</strong> '
            f'{_esc(finding["evidence_assessment"]["rationale"])}'
            + (
                " Capped because "
                + _esc("; ".join(finding["evidence_assessment"]["caps_applied"]))
                + "."
                if finding["evidence_assessment"]["caps_applied"]
                else ""
            )
            + f' Site-verified: {_esc(finding["evidence_assessment"]["site_verified"])}.</p>',
        ]
        if finding["assumptions"]:
            blocks.append(
                "<p><strong>Assumptions</strong></p><ul>"
                + "".join(f"<li>{_esc(a)}</li>" for a in finding["assumptions"])
                + "</ul>"
            )
        if finding["limitations"]:
            blocks.append(
                "<p><strong>Limitations</strong></p><ul>"
                + "".join(f"<li>{_esc(l)}</li>" for l in finding["limitations"])
                + "</ul>"
            )
        if finding["recommended_action"]:
            blocks.append(
                "<p><strong>Field validation</strong></p><ul>"
                + "".join(f"<li>{_esc(a)}</li>" for a in finding["recommended_action"])
                + "</ul>"
            )
        sections.append(
            f'<div class="finding {_esc(finding["claim_type"])}">' + "".join(blocks) + "</div>"
        )

    sections.append("<h2>Evidence rules applied</h2>")
    sections.append(
        "<ul>" + "".join(f"<li>{_esc(rule)}</li>" for rule in analysis["evidence_rules"]) + "</ul>"
    )

    status_class = "ok" if analysis["status"] == "ok" else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Intralogistics flow analysis — {_esc(meta.get('dataset_id'))}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header class="report">
<h1>Intralogistics flow analysis</h1>
<p class="subtitle">Dataset <code>{_esc(meta.get('dataset_id'))}</code> · site
<code>{_esc(meta.get('site_id') or 'not declared')}</code></p>
<p>
<span class="badge {status_class}">status {_esc(analysis['status'])}</span>
<span class="badge">engine {_esc(analysis['engine_version'])}</span>
<span class="badge">schema {_esc(analysis['schema_version'])}</span>
<span class="badge">source {_esc(meta.get('source') or 'not declared')}</span>
</p>
{banner}
</header>
{''.join(sections)}
<footer class="report">
<p>Pickwege sichtbar machen. Lagerplätze datenbasiert verbessern. — Der Algorithmus rechnet.
Die KI ordnet ein.</p>
<p>No individual worker data is processed, stored or reported. The unit of analysis is the
order, the route, the item, the location and the zone. This report is a decision aid; every
proposed change must be validated on site before anything is moved.</p>
</footer>
</main>
</body>
</html>
"""


# ------------------------------------------------------------------ writing


def write_reports(
    result: AnalysisResult,
    output_dir: Path,
    spaghetti_order: Optional[str] = None,
    spaghetti_top: Optional[int] = None,
) -> Dict[str, Path]:
    """Write every artefact of an analysis run and return their paths."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset = result.dataset
    fictional = bool(dataset.meta.fictional)

    flow_map = render_flow_map(dataset, result.edge_rows, result.location_rows, fictional)
    spaghetti = render_spaghetti(
        dataset,
        result.routing,
        order_id=spaghetti_order,
        top=spaghetti_top or int(dataset.config.get("spaghetti_top_orders") or 10),
        fictional=fictional,
    )
    aggregate = render_spaghetti(dataset, result.routing, aggregate=True, fictional=fictional)

    paths = {
        "analysis_json": write_json(output_dir / "analysis.json", result.analysis),
        "recommendations_csv": write_recommendations_csv(
            output_dir / "recommendations.csv", result.analysis["recommendations"]
        ),
        "report_md": write_text(output_dir / "report.md", render_markdown(result)),
        "flow_map_svg": write_text(output_dir / "flow-map.svg", flow_map),
        "spaghetti_svg": write_text(output_dir / "spaghetti.svg", spaghetti),
        "spaghetti_aggregate_svg": write_text(
            output_dir / "spaghetti-aggregate.svg", aggregate
        ),
    }
    paths["report_html"] = write_text(
        output_dir / "report.html", render_html(result, flow_map, spaghetti)
    )
    paths["moves_json"] = write_json(
        output_dir / "moves.json", result.slotting.moves_document()
    )
    return paths
