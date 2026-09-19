"""Per-category evidence assessment.

Until 0.1.1 a single coverage-and-sample-size rule labelled every finding, so a
co-pick pair seen twice inherited the confidence of a distance calculation over
fifteen orders. This module replaces that with one deterministic function per
finding family. Each returns an :class:`EvidenceAssessment` carrying:

``quality``       the compatibility label: high, medium, low or insufficient
``factors``       every input that produced it, so the label can be recomputed
``site_verified`` whether a human confirmed the claim on the floor
``rationale``     one sentence naming the rule that decided the level

A label is **not a probability**. It is the name of a rung on a documented
ladder, and the rungs are stated in
``skills/intralogistics-flow-analyzer/references/evidence-rules.md``.

Two caps are mandatory and cannot be bought back by sample size:

* an unverified simulation never reaches ``high``;
* complete constraint data that nobody checked on site never reaches ``high``.

Because the Community Edition has no channel through which a site verification
could be recorded, ``site_verified`` is ``False`` everywhere it appears. That is
a statement about the edition, not a placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .models import DEFAULT_SEQUENCE_BASIS

#: Ordered worst to best. ``_cap`` and ``_ladder`` rely on this order.
EVIDENCE_LEVELS = ("insufficient", "low", "medium", "high")

#: Documented thresholds. Every one of them is echoed into
#: ``analysis.thresholds.evidence`` so a reader can recompute any label by hand.
#: A dataset may override any key through ``config.evidence_thresholds``.
DEFAULT_EVIDENCE_THRESHOLDS: Dict[str, float] = {
    # Routes, distances, locations and aisle segments
    "route_orders_high": 30,
    "route_orders_medium": 10,
    "route_orders_low": 3,
    "route_coverage_high": 95.0,
    "route_coverage_medium": 80.0,
    # ABC
    "abc_orders_high": 30,
    "abc_orders_medium": 10,
    "abc_orders_low": 3,
    "abc_period_days_high": 28,
    "abc_period_days_medium": 7,
    # XYZ, per bucket type
    "xyz_day_buckets_high": 90,
    "xyz_day_buckets_medium": 28,
    "xyz_week_buckets_high": 26,
    "xyz_week_buckets_medium": 12,
    "xyz_timestamp_rate_high": 0.98,
    "xyz_timestamp_rate_medium": 0.90,
    # Co-pick affinity
    "affinity_orders_high": 200,
    "affinity_orders_medium": 50,
    "affinity_co_picks_high": 30,
    "affinity_co_picks_medium": 10,
    "affinity_support_floor": 0.02,
    # Simulation
    "simulation_comparable_high": 30,
    "simulation_comparable_medium": 10,
    "simulation_affected_high": 30,
    "simulation_affected_medium": 10,
    # Constraints
    "constraint_checks_expected": 9,
    "constraint_unknowns_medium": 2,
}

#: Sequence provenance that can support a statement about what was walked.
CONFIRMED_SEQUENCE_BASES = ("scan_confirmed", "observed")


def thresholds_for(config: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Documented defaults, overridden by ``config.evidence_thresholds``."""
    resolved = dict(DEFAULT_EVIDENCE_THRESHOLDS)
    overrides = (config or {}).get("evidence_thresholds") or {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in resolved:
                try:
                    resolved[key] = float(value)
                except (TypeError, ValueError):
                    continue
    return resolved


@dataclass
class EvidenceAssessment:
    """The label, everything that produced it, and why it stopped there."""

    quality: str
    factors: Dict[str, Any] = field(default_factory=dict)
    site_verified: bool = False
    rationale: str = ""
    caps_applied: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "factors": dict(self.factors),
            "site_verified": self.site_verified,
            "rationale": self.rationale,
            "caps_applied": list(self.caps_applied),
        }


def _ladder(*rungs: tuple) -> str:
    """First matching rung wins; ``rungs`` are ``(condition, level)`` pairs."""
    for condition, level in rungs:
        if condition:
            return level
    return "insufficient"


def _cap(level: str, ceiling: str) -> str:
    return level if EVIDENCE_LEVELS.index(level) <= EVIDENCE_LEVELS.index(ceiling) else ceiling


def _apply_caps(level: str, caps: Sequence[tuple]) -> tuple:
    """Apply ``(condition, ceiling, reason)`` caps; return ``(level, reasons)``."""
    applied: List[str] = []
    for condition, ceiling, reason in caps:
        if condition and EVIDENCE_LEVELS.index(level) > EVIDENCE_LEVELS.index(ceiling):
            level = _cap(level, ceiling)
            applied.append(reason)
    return level, applied


# ------------------------------------------------------------------ routes


def quality_for_route_finding(
    routed_orders: int,
    coverage_percent: Optional[float],
    sequence_basis: str = DEFAULT_SEQUENCE_BASIS,
    graph_complete: bool = True,
    thresholds: Optional[Dict[str, float]] = None,
) -> EvidenceAssessment:
    """Distances, per-location attribution and segment traversals.

    The sequence provenance caps the result: a route modelled from a *planned*
    or undeclared sequence describes an intention, not a movement.
    """
    t = thresholds or DEFAULT_EVIDENCE_THRESHOLDS
    coverage = coverage_percent if coverage_percent is not None else 0.0
    level = _ladder(
        (
            routed_orders >= t["route_orders_high"] and coverage >= t["route_coverage_high"],
            "high",
        ),
        (
            routed_orders >= t["route_orders_medium"] and coverage >= t["route_coverage_medium"],
            "medium",
        ),
        (routed_orders >= t["route_orders_low"], "low"),
    )
    level, caps = _apply_caps(
        level,
        [
            (
                sequence_basis not in CONFIRMED_SEQUENCE_BASES,
                "medium",
                f"sequence_basis is '{sequence_basis}', so the route is modelled from a "
                "sequence that was not confirmed at the pick face",
            ),
            (
                not graph_complete,
                "low",
                "at least one order could not be routed on the declared graph",
            ),
        ],
    )
    return EvidenceAssessment(
        quality=level,
        factors={
            "routed_orders": routed_orders,
            "route_coverage_percent": coverage_percent,
            "sequence_basis": sequence_basis,
            "graph_complete": graph_complete,
        },
        site_verified=False,
        rationale=(
            f"{routed_orders} routed orders at {coverage_percent}% coverage on a "
            f"'{sequence_basis}' sequence."
        ),
        caps_applied=caps,
    )


# --------------------------------------------------------------------- ABC


def quality_for_abc_finding(
    total_orders: int,
    period_days: Optional[int],
    basis: str,
    basis_data_complete: bool = True,
    thresholds: Optional[Dict[str, float]] = None,
) -> EvidenceAssessment:
    """ABC ranks demand volume over an observed window."""
    t = thresholds or DEFAULT_EVIDENCE_THRESHOLDS
    days = period_days if period_days is not None else 0
    level = _ladder(
        (
            total_orders >= t["abc_orders_high"] and days >= t["abc_period_days_high"],
            "high",
        ),
        (
            total_orders >= t["abc_orders_medium"] and days >= t["abc_period_days_medium"],
            "medium",
        ),
        (total_orders >= t["abc_orders_low"], "low"),
    )
    level, caps = _apply_caps(
        level,
        [
            (
                not basis_data_complete,
                "low",
                f"the '{basis}' basis is incomplete for at least one pick line",
            ),
        ],
    )
    return EvidenceAssessment(
        quality=level,
        factors={
            "total_orders": total_orders,
            "period_days": period_days,
            "abc_basis": basis,
            "basis_data_complete": basis_data_complete,
        },
        site_verified=False,
        rationale=(
            f"{total_orders} orders over {period_days} days on the '{basis}' basis."
        ),
        caps_applied=caps,
    )


# --------------------------------------------------------------------- XYZ


def quality_for_xyz_finding(
    buckets: int,
    bucket_type: str,
    timestamp_rate: float,
    minimum_buckets: int,
    computed: bool = True,
    thresholds: Optional[Dict[str, float]] = None,
) -> EvidenceAssessment:
    """XYZ measures variability, which needs many periods, not many orders."""
    t = thresholds or DEFAULT_EVIDENCE_THRESHOLDS
    if not computed:
        return EvidenceAssessment(
            quality="insufficient",
            factors={
                "buckets": buckets,
                "bucket_type": bucket_type,
                "timestamp_rate": timestamp_rate,
                "minimum_buckets": minimum_buckets,
                "computed": False,
            },
            site_verified=False,
            rationale="Variability was not computed, so there is nothing to qualify.",
        )
    if bucket_type == "week":
        high_buckets = t["xyz_week_buckets_high"]
        medium_buckets = t["xyz_week_buckets_medium"]
    else:
        high_buckets = t["xyz_day_buckets_high"]
        medium_buckets = t["xyz_day_buckets_medium"]
    level = _ladder(
        (
            buckets >= high_buckets and timestamp_rate >= t["xyz_timestamp_rate_high"],
            "high",
        ),
        (
            buckets >= medium_buckets and timestamp_rate >= t["xyz_timestamp_rate_medium"],
            "medium",
        ),
        (buckets >= minimum_buckets, "low"),
    )
    return EvidenceAssessment(
        quality=level,
        factors={
            "buckets": buckets,
            "bucket_type": bucket_type,
            "timestamp_rate": round(timestamp_rate, 4),
            "minimum_buckets": minimum_buckets,
            "computed": True,
        },
        site_verified=False,
        rationale=(
            f"{buckets} {bucket_type} buckets with {round(100.0 * timestamp_rate, 1)}% of pick "
            f"lines timestamped (medium needs {int(medium_buckets)} buckets, high "
            f"{int(high_buckets)})."
        ),
    )


# ---------------------------------------------------------------- affinity


def quality_for_affinity_finding(
    total_orders: int,
    co_pick_count: int,
    support: float,
    minimum_co_picks: int,
    thresholds: Optional[Dict[str, float]] = None,
) -> EvidenceAssessment:
    """A pair is only as solid as the number of orders that actually contain it."""
    t = thresholds or DEFAULT_EVIDENCE_THRESHOLDS
    level = _ladder(
        (
            total_orders >= t["affinity_orders_high"]
            and co_pick_count >= t["affinity_co_picks_high"],
            "high",
        ),
        (
            total_orders >= t["affinity_orders_medium"]
            and co_pick_count >= t["affinity_co_picks_medium"],
            "medium",
        ),
        (co_pick_count >= max(2, minimum_co_picks), "low"),
    )
    level, caps = _apply_caps(
        level,
        [
            (
                support < t["affinity_support_floor"],
                "low",
                f"support {round(support, 4)} is below the "
                f"{t['affinity_support_floor']} floor, so the pair is rare",
            ),
        ],
    )
    return EvidenceAssessment(
        quality=level,
        factors={
            "total_orders": total_orders,
            "co_pick_count": co_pick_count,
            "support": round(support, 6),
            "minimum_co_picks": minimum_co_picks,
        },
        site_verified=False,
        rationale=(
            f"{co_pick_count} co-picks across {total_orders} orders "
            f"(support {round(100.0 * support, 2)}%)."
        ),
        caps_applied=caps,
    )


# -------------------------------------------------------------- simulation


def quality_for_simulation_finding(
    coverage_percent: Optional[float],
    comparable_orders: int,
    affected_orders: int,
    constraints_complete: bool,
    sequence_basis: str = DEFAULT_SEQUENCE_BASIS,
    site_verified: bool = False,
    thresholds: Optional[Dict[str, float]] = None,
) -> EvidenceAssessment:
    """A replayed scenario. Never ``high`` until somebody checks the floor."""
    t = thresholds or DEFAULT_EVIDENCE_THRESHOLDS
    coverage = coverage_percent if coverage_percent is not None else 0.0
    level = _ladder(
        (
            coverage >= t["route_coverage_high"]
            and comparable_orders >= t["simulation_comparable_high"]
            and affected_orders >= t["simulation_affected_high"],
            "high",
        ),
        (
            coverage >= t["route_coverage_medium"]
            and comparable_orders >= t["simulation_comparable_medium"]
            and affected_orders >= t["simulation_affected_medium"],
            "medium",
        ),
        (affected_orders >= 1, "low"),
    )
    level, caps = _apply_caps(
        level,
        [
            (
                not site_verified,
                "medium",
                "the scenario has not been verified on site, and a simulated reduction is "
                "not a realised saving",
            ),
            (
                not constraints_complete,
                "low",
                "at least one constraint value used by the scenario is unknown",
            ),
            (
                sequence_basis == "unknown",
                "low",
                "the replayed pick sequence has undeclared provenance",
            ),
        ],
    )
    return EvidenceAssessment(
        quality=level,
        factors={
            "route_coverage_percent": coverage_percent,
            "comparable_orders": comparable_orders,
            "affected_orders": affected_orders,
            "constraints_complete": constraints_complete,
            "sequence_basis": sequence_basis,
        },
        site_verified=site_verified,
        rationale=(
            f"{affected_orders} of {comparable_orders} comparable orders change at "
            f"{coverage_percent}% route coverage; the scenario is not site-verified."
        ),
        caps_applied=caps,
    )


# -------------------------------------------------------------- constraints


def quality_for_constraint_finding(
    checks_evaluated: int,
    unknown_checks: int,
    site_verified: bool = False,
    thresholds: Optional[Dict[str, float]] = None,
) -> EvidenceAssessment:
    """Master-data completeness. Complete but unchecked is still not ``high``."""
    t = thresholds or DEFAULT_EVIDENCE_THRESHOLDS
    expected = int(t["constraint_checks_expected"])
    level = _ladder(
        (checks_evaluated >= expected and unknown_checks == 0, "high"),
        (
            checks_evaluated >= expected
            and unknown_checks <= t["constraint_unknowns_medium"],
            "medium",
        ),
        (checks_evaluated > 0, "low"),
    )
    level, caps = _apply_caps(
        level,
        [
            (
                not site_verified,
                "medium",
                "constraint values come from the dataset and were not confirmed on site",
            ),
        ],
    )
    return EvidenceAssessment(
        quality=level,
        factors={
            "checks_evaluated": checks_evaluated,
            "checks_expected": expected,
            "unknown_checks": unknown_checks,
        },
        site_verified=site_verified,
        rationale=(
            f"{checks_evaluated} of {expected} constraint checks evaluated, "
            f"{unknown_checks} of them unknown; not site-verified."
        ),
        caps_applied=caps,
    )


def period_days(period_start: Optional[str], period_end: Optional[str]) -> Optional[int]:
    """Inclusive day count of the declared period, or ``None`` when undeclared."""
    from datetime import datetime

    def parse(value: Optional[str]):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    start, end = parse(period_start), parse(period_end)
    if start is None or end is None:
        return None
    return (end.date() - start.date()).days + 1
