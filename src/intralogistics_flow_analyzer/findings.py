"""Findings: statements that are traceable back to a number the engine produced.

A finding never asserts a cause. It reports what was observed or calculated and
names, separately, the assumptions it rests on and the limits of the evidence.

``claim_type``
    observed    counted directly in the input records
    calculated  derived from the graph and the input records
    simulated   produced by replaying history against a modified slotting
    hypothesis  a question raised by the data, to be verified on site

Every finding carries two evidence fields:

``evidence_quality``    the compatibility label (high, medium, low, insufficient)
``evidence_assessment`` the factors, the site-verification flag and the rationale
                        that produced that label

The label is decided by the finding's own family, in
:mod:`intralogistics_flow_analyzer.evidence`. It is never a probability.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .affinity import AFFINITY_DISCLAIMER
from .classification import XYZ_NOT_COMPUTED
from .evidence import (
    EvidenceAssessment,
    period_days,
    quality_for_abc_finding,
    quality_for_affinity_finding,
    quality_for_constraint_finding,
    quality_for_route_finding,
    quality_for_simulation_finding,
    quality_for_xyz_finding,
    thresholds_for,
)
from .models import CLAIM_TYPES, EVIDENCE_QUALITIES

CONGESTION_WORDING = (
    "This aisle shows a high concentration of modelled traversals. The presence of "
    "congestion must be verified on site or with time-stamped movement data."
)

#: Wording that keeps a modelled figure from reading as a measurement.
DISTANCE_WORDING = (
    "Distances are modelled: they are the shortest permitted path on the declared aisle "
    "graph between consecutive pick access nodes, in the given sequence. They are not a "
    "measurement of the path actually walked."
)


class FindingBuilder:
    def __init__(self) -> None:
        self._findings: List[Dict[str, Any]] = []

    def add(
        self,
        claim_type: str,
        statement: str,
        evidence: Sequence[Dict[str, Any]],
        assessment: EvidenceAssessment,
        assumptions: Optional[Sequence[str]] = None,
        limitations: Optional[Sequence[str]] = None,
        recommended_action: Optional[Sequence[str]] = None,
    ) -> None:
        assert claim_type in CLAIM_TYPES, claim_type
        assert assessment.quality in EVIDENCE_QUALITIES, assessment.quality
        self._findings.append(
            {
                "finding_id": f"F-{len(self._findings) + 1:03d}",
                "claim_type": claim_type,
                "statement": statement,
                "evidence": [dict(e) for e in evidence],
                "assumptions": list(assumptions or []),
                "limitations": list(limitations or []),
                "recommended_action": list(recommended_action or []),
                # Kept for compatibility with 0.1.0 consumers.
                "evidence_quality": assessment.quality,
                "evidence_assessment": assessment.to_dict(),
            }
        )

    def result(self) -> List[Dict[str, Any]]:
        return list(self._findings)


def build_findings(
    *,
    meta: Dict[str, Any],
    config: Dict[str, Any],
    metrics: Dict[str, Any],
    data_quality: Dict[str, Any],
    location_rows: Sequence[Dict[str, Any]],
    sku_rows: Sequence[Dict[str, Any]],
    edge_rows: Sequence[Dict[str, Any]],
    abc_rows: Sequence[Dict[str, Any]],
    xyz_rows: Sequence[Dict[str, Any]],
    xyz_marker: Optional[str],
    affinity_rows: Sequence[Dict[str, Any]],
    recommendations: Sequence[Dict[str, Any]],
    rejected_candidates: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    builder = FindingBuilder()
    thresholds = thresholds_for(config)

    coverage = data_quality.get("route_coverage_percent")
    routed_orders = int(data_quality.get("orders_routed") or 0)
    total_orders = int(data_quality.get("orders_total") or 0)
    excluded = int(data_quality.get("orders_excluded") or 0)
    graph_complete = excluded == 0
    sequence_basis = str(meta.get("sequence_basis") or "unknown")
    distance_basis = str(meta.get("distance_basis") or "declared_graph_shortest_path")
    days = period_days(meta.get("period_start"), meta.get("period_end"))

    picks_total = int(data_quality.get("picks_total") or 0)
    picks_with_timestamp = int(data_quality.get("picks_with_timestamp") or 0)
    timestamp_rate = (picks_with_timestamp / picks_total) if picks_total else 0.0

    def route_assessment() -> EvidenceAssessment:
        return quality_for_route_finding(
            routed_orders=routed_orders,
            coverage_percent=coverage,
            sequence_basis=sequence_basis,
            graph_complete=graph_complete,
            thresholds=thresholds,
        )

    # ---------------------------------------------------------------- coverage
    builder.add(
        claim_type="observed",
        statement=(
            f"{routed_orders} of {total_orders} orders ({coverage}% of orders) could be "
            f"reconstructed on the aisle graph; {excluded} were excluded. The pick sequence is "
            f"declared as '{sequence_basis}' and distances as '{distance_basis}'."
        ),
        evidence=[
            {
                "type": "data_quality",
                "orders_total": total_orders,
                "orders_routed": routed_orders,
                "orders_excluded": excluded,
                "sequence_basis": sequence_basis,
                "distance_basis": distance_basis,
            }
        ],
        assessment=route_assessment(),
        limitations=[
            "Excluded orders contribute no distance figure and are absent from every "
            "distance metric in this report.",
        ],
    )

    # ------------------------------------------------------ distance profile
    if metrics.get("total_distance_m") is not None and routed_orders > 0:
        builder.add(
            claim_type="calculated",
            statement=(
                f"Modelled picking travel over the observed period totals "
                f"{metrics['total_distance_m']} m across {routed_orders} orders "
                f"(median {metrics.get('median_distance_per_order_m')} m per order, "
                f"p90 {metrics.get('p90_distance_per_order_m')} m)."
            ),
            evidence=[
                {
                    "type": "metrics",
                    "distance_basis": distance_basis,
                    **{
                        k: metrics.get(k)
                        for k in (
                            "total_distance_m",
                            "average_distance_per_order_m",
                            "median_distance_per_order_m",
                            "p90_distance_per_order_m",
                            "average_distance_per_pick_m",
                        )
                    },
                }
            ],
            assessment=route_assessment(),
            assumptions=[
                "Travel follows the shortest permitted path on the declared aisle graph.",
                f"The pick sequence used for reconstruction is '{sequence_basis}'.",
            ],
            limitations=[
                DISTANCE_WORDING,
                "A long modelled route is not by itself evidence of avoidable travel.",
            ],
        )

    # ----------------------------------------------------- dominant locations
    ranked_locations = sorted(
        [row for row in location_rows if row.get("visits")],
        key=lambda r: (-(r.get("visit_share_percent") or 0.0), r["location_id"]),
    )
    if ranked_locations:
        top = ranked_locations[0]
        distances = sorted(
            [
                row.get("distance_from_start_node_m")
                for row in location_rows
                if row.get("location_type") == "pick"
                and row.get("distance_from_start_node_m") is not None
            ]
        )
        quartile_note = ""
        if distances and top.get("distance_from_start_node_m") is not None:
            index = int(0.75 * (len(distances) - 1))
            if top["distance_from_start_node_m"] >= distances[index]:
                quartile_note = (
                    " It sits in the quartile of pick locations furthest from the start node."
                )
        builder.add(
            claim_type="calculated",
            statement=(
                f"Location {top['location_id']} accounts for {top['visit_share_percent']}% of "
                f"recorded location visits and {top['distance_share_percent']}% of attributable "
                f"modelled travel distance.{quartile_note}"
            ),
            evidence=[{"type": "location_metrics", **top}],
            assessment=route_assessment(),
            limitations=[
                "A high visit frequency is not by itself evidence of a badly placed item.",
                "Distance attribution charges the arriving leg to the location reached; it is "
                "an accounting convention, not a measurement.",
            ],
            recommended_action=[
                f"Check on site whether {top['location_id']} is reachable and ergonomic at its "
                "observed pick rate."
            ],
        )

    # ------------------------------------------------------------ aisle flows
    busiest = [row for row in edge_rows if row.get("traversals")]
    if busiest:
        edge = busiest[0]
        builder.add(
            claim_type="calculated",
            statement=(
                f"Aisle segment {edge['edge_id']} ({edge['from_node']}-{edge['to_node']}) carries "
                f"{edge['traversal_share_percent']}% of all modelled segment traversals "
                f"({edge['traversals']} passes)."
            ),
            evidence=[{"type": "edge_flow", **edge}],
            assessment=route_assessment(),
            limitations=[CONGESTION_WORDING],
            recommended_action=[
                "Observe the segment during a peak shift before drawing any conclusion about "
                "congestion."
            ],
        )

    # ------------------------------------------------------------------- ABC
    a_items = [row for row in abc_rows if row.get("abc_class") == "A"]
    if a_items and abc_rows:
        share = round(sum(row["share_percent"] for row in a_items), 2)
        builder.add(
            claim_type="calculated",
            statement=(
                f"{len(a_items)} of {len(abc_rows)} SKUs are class A on basis "
                f"'{abc_rows[0]['basis']}' and together account for {share}% of that basis."
            ),
            evidence=[{"type": "abc", "class_a_skus": [row["sku"] for row in a_items]}],
            assessment=quality_for_abc_finding(
                total_orders=total_orders,
                period_days=days,
                basis=str(abc_rows[0]["basis"]),
                basis_data_complete=True,
                thresholds=thresholds,
            ),
            assumptions=["The observed period is representative of normal demand."],
            limitations=[
                "ABC ranks demand volume only; it says nothing about demand stability."
            ],
        )

    # ------------------------------------------------------------------- XYZ
    bucket_type = str(config.get("xyz_time_bucket") or "day")
    minimum_buckets = int(config.get("xyz_minimum_buckets") or 7)
    if xyz_marker == XYZ_NOT_COMPUTED:
        builder.add(
            claim_type="observed",
            statement=(
                "XYZ variability was not computed: the observed period contains fewer time "
                "buckets than the configured minimum. No SKU has been labelled X, Y or Z."
            ),
            evidence=[{"type": "xyz", "marker": XYZ_NOT_COMPUTED}],
            assessment=quality_for_xyz_finding(
                buckets=0,
                bucket_type=bucket_type,
                timestamp_rate=timestamp_rate,
                minimum_buckets=minimum_buckets,
                computed=False,
                thresholds=thresholds,
            ),
            limitations=[
                "Absence of a variability class must not be read as stable or unstable demand."
            ],
            recommended_action=["Extend the export period and re-run the analysis."],
        )
    elif xyz_rows:
        classified = [row for row in xyz_rows if row.get("xyz_class")]
        if classified:
            builder.add(
                claim_type="calculated",
                statement=(
                    f"Demand variability was computed over {classified[0]['buckets']} "
                    f"{classified[0]['bucket_type']} buckets using the coefficient of variation "
                    f"for {len(classified)} SKUs."
                ),
                evidence=[{"type": "xyz", "skus": [row["sku"] for row in classified]}],
                assessment=quality_for_xyz_finding(
                    buckets=int(classified[0]["buckets"]),
                    bucket_type=str(classified[0]["bucket_type"]),
                    timestamp_rate=timestamp_rate,
                    minimum_buckets=minimum_buckets,
                    computed=True,
                    thresholds=thresholds,
                ),
                limitations=[
                    "The coefficient of variation is sensitive to short observation periods."
                ],
            )

    # -------------------------------------------------------------- affinity
    if affinity_rows:
        pair = affinity_rows[0]
        confidence = pair.get("confidence_a_to_b")
        if confidence is not None:
            builder.add(
                claim_type="calculated",
                statement=(
                    f"SKU {pair['sku_a']} and SKU {pair['sku_b']} appear together in "
                    f"{pair['co_pick_count']} orders, that is {round(100.0 * confidence, 2)}% of "
                    f"the orders containing {pair['sku_a']}."
                ),
                evidence=[
                    {
                        "type": "affinity",
                        **{
                            k: pair[k]
                            for k in (
                                "sku_a",
                                "sku_b",
                                "co_pick_count",
                                "support",
                                "confidence_a_to_b",
                                "confidence_b_to_a",
                                "lift",
                            )
                        },
                    }
                ],
                assessment=quality_for_affinity_finding(
                    total_orders=int(pair.get("total_orders") or total_orders),
                    co_pick_count=int(pair.get("co_pick_count") or 0),
                    support=float(pair.get("support") or 0.0),
                    minimum_co_picks=int(config.get("affinity_minimum_co_picks") or 2),
                    thresholds=thresholds,
                ),
                limitations=[AFFINITY_DISCLAIMER],
                recommended_action=[
                    "Treat the pair as a question for the planner, not as an instruction to "
                    "co-locate."
                ],
            )

    # -------------------------------------------------------- recommendations
    for recommendation in recommendations:
        checks = recommendation.get("constraint_checks") or {}
        unknown_checks = len([v for v in checks.values() if v == "insufficient_data"])
        builder.add(
            claim_type="simulated",
            statement=(
                f"{recommendation['recommendation_id']}: swapping {recommendation['sku_a']} "
                f"({recommendation['from_a']} -> {recommendation['to_a']}) with "
                f"{recommendation['sku_b']} ({recommendation['from_b']} -> "
                f"{recommendation['to_b']}) gives an estimated pick-distance reduction of "
                f"{recommendation['estimated_reduction_m']} m "
                f"({recommendation['estimated_reduction_percent']}%) over "
                f"{recommendation['affected_orders']} affected historical orders."
            ),
            evidence=[
                {
                    "type": "simulation",
                    "recommendation_id": recommendation["recommendation_id"],
                    "baseline_distance_m": recommendation["baseline_distance_m"],
                    "simulated_distance_m": recommendation["simulated_distance_m"],
                    "constraint_checks": checks,
                    "affected_order_ids": recommendation["affected_order_ids"],
                }
            ],
            assessment=quality_for_simulation_finding(
                coverage_percent=coverage,
                comparable_orders=routed_orders,
                affected_orders=int(recommendation.get("affected_orders") or 0),
                constraints_complete=unknown_checks == 0,
                sequence_basis=sequence_basis,
                site_verified=False,
                thresholds=thresholds,
            ),
            assumptions=list(recommendation.get("assumptions") or []),
            limitations=list(recommendation.get("limitations") or []),
            recommended_action=[
                "Verify stock volume, access and ergonomics on site before moving anything.",
                "Re-measure after the move; a simulated reduction is not a realised saving.",
            ],
        )

    # --------------------------------------------------- rejected candidates
    blocked = [r for r in rejected_candidates if r.get("status") == "constraint_violation"]
    if blocked:
        names = ", ".join(sorted({f"{r['sku_a']}/{r['sku_b']}" for r in blocked})[:5])
        checks = blocked[0].get("constraint_checks") or {}
        builder.add(
            claim_type="calculated",
            statement=(
                f"{len(blocked)} candidate swaps were rejected because at least one hard "
                f"constraint failed (examples: {names})."
            ),
            evidence=[
                {
                    "type": "rejected_candidate",
                    "sku_a": r["sku_a"],
                    "sku_b": r["sku_b"],
                    "rejected_because": r.get("rejected_because"),
                }
                for r in blocked[:10]
            ],
            assessment=quality_for_constraint_finding(
                checks_evaluated=len(checks),
                unknown_checks=len([v for v in checks.values() if v == "insufficient_data"]),
                site_verified=False,
                thresholds=thresholds,
            ),
            limitations=[
                "Constraint data comes from the dataset; it has not been verified on site."
            ],
            recommended_action=[
                "Confirm the blocking constraint on site before treating the location as "
                "unusable for that item."
            ],
        )

    insufficient = [
        r for r in rejected_candidates if r.get("status") == "insufficient_constraint_data"
    ]
    if insufficient:
        checks = insufficient[0].get("constraint_checks") or {}
        builder.add(
            claim_type="observed",
            statement=(
                f"{len(insufficient)} candidate swaps could not be assessed because at least one "
                "required constraint value is missing from the dataset."
            ),
            evidence=[
                {
                    "type": "rejected_candidate",
                    "sku_a": r["sku_a"],
                    "sku_b": r["sku_b"],
                    "missing": r.get("rejected_because"),
                }
                for r in insufficient[:10]
            ],
            assessment=quality_for_constraint_finding(
                checks_evaluated=len(checks),
                unknown_checks=max(
                    1, len([v for v in checks.values() if v == "insufficient_data"])
                ),
                site_verified=False,
                thresholds=thresholds,
            ),
            limitations=["No relocation is proposed while a critical constraint is unknown."],
            recommended_action=[
                "Complete the item master and location master for the listed records, then "
                "re-run the analysis."
            ],
        )

    if not recommendations:
        builder.add(
            claim_type="hypothesis",
            statement=(
                "No pair swap in this dataset clears the configured minimum improvement "
                "threshold. The current pick-face layout may already be reasonable for the "
                "observed order profile, or the evidence may be too thin to show a difference."
            ),
            evidence=[{"type": "slotting", "accepted": 0}],
            assessment=quality_for_simulation_finding(
                coverage_percent=coverage,
                comparable_orders=routed_orders,
                affected_orders=0,
                constraints_complete=False,
                sequence_basis=sequence_basis,
                site_verified=False,
                thresholds=thresholds,
            ),
            limitations=[
                "Schema 1.0 evaluates two-location swaps only; other layout changes are out of "
                "scope and have not been tested."
            ],
            recommended_action=[
                "Widen the observation period or review the layout with a planner."
            ],
        )

    return builder.result()
