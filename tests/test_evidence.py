"""Per-category evidence assessment: every family, every level, every cap."""

from __future__ import annotations

import unittest

from fixtures import example_dataset, minimal_document, mutate

from intralogistics_flow_analyzer.evidence import (
    DEFAULT_EVIDENCE_THRESHOLDS,
    EVIDENCE_LEVELS,
    period_days,
    quality_for_abc_finding,
    quality_for_affinity_finding,
    quality_for_constraint_finding,
    quality_for_route_finding,
    quality_for_simulation_finding,
    quality_for_xyz_finding,
    thresholds_for,
)
from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.reporting import analyse


class RouteEvidenceTest(unittest.TestCase):
    def test_high_needs_scale_coverage_and_a_confirmed_sequence(self) -> None:
        assessment = quality_for_route_finding(40, 100.0, "scan_confirmed", True)
        self.assertEqual(assessment.quality, "high")
        self.assertEqual(assessment.caps_applied, [])

    def test_observed_sequence_also_supports_high(self) -> None:
        self.assertEqual(quality_for_route_finding(40, 100.0, "observed").quality, "high")

    def test_medium_for_a_moderate_sample(self) -> None:
        self.assertEqual(
            quality_for_route_finding(15, 90.0, "scan_confirmed").quality, "medium"
        )

    def test_low_for_a_thin_sample(self) -> None:
        self.assertEqual(quality_for_route_finding(4, 50.0, "scan_confirmed").quality, "low")

    def test_insufficient_below_the_floor(self) -> None:
        self.assertEqual(quality_for_route_finding(1, 10.0, "scan_confirmed").quality, "insufficient")

    def test_a_planned_sequence_caps_at_medium(self) -> None:
        assessment = quality_for_route_finding(400, 100.0, "planned")
        self.assertEqual(assessment.quality, "medium")
        self.assertTrue(any("planned" in reason for reason in assessment.caps_applied))

    def test_an_unknown_sequence_caps_at_medium(self) -> None:
        self.assertEqual(quality_for_route_finding(400, 100.0, "unknown").quality, "medium")

    def test_an_incomplete_graph_caps_at_low(self) -> None:
        assessment = quality_for_route_finding(400, 100.0, "scan_confirmed", graph_complete=False)
        self.assertEqual(assessment.quality, "low")

    def test_the_factors_are_reported(self) -> None:
        factors = quality_for_route_finding(40, 99.0, "observed").factors
        self.assertEqual(factors["routed_orders"], 40)
        self.assertEqual(factors["sequence_basis"], "observed")


class AbcEvidenceTest(unittest.TestCase):
    def test_levels(self) -> None:
        self.assertEqual(quality_for_abc_finding(50, 60, "pick_lines").quality, "high")
        self.assertEqual(quality_for_abc_finding(15, 10, "pick_lines").quality, "medium")
        self.assertEqual(quality_for_abc_finding(5, 2, "pick_lines").quality, "low")
        self.assertEqual(quality_for_abc_finding(1, 1, "pick_lines").quality, "insufficient")

    def test_a_short_period_prevents_high_however_many_orders(self) -> None:
        self.assertEqual(quality_for_abc_finding(5000, 3, "pick_lines").quality, "low")

    def test_incomplete_basis_data_caps_at_low(self) -> None:
        assessment = quality_for_abc_finding(50, 60, "units", basis_data_complete=False)
        self.assertEqual(assessment.quality, "low")
        self.assertTrue(assessment.caps_applied)


class XyzEvidenceTest(unittest.TestCase):
    def test_not_computed_is_insufficient(self) -> None:
        assessment = quality_for_xyz_finding(0, "day", 1.0, 7, computed=False)
        self.assertEqual(assessment.quality, "insufficient")
        self.assertIn("not computed", assessment.rationale)

    def test_daily_buckets_need_many_periods_for_high(self) -> None:
        self.assertEqual(quality_for_xyz_finding(120, "day", 1.0, 7).quality, "high")
        self.assertEqual(quality_for_xyz_finding(40, "day", 0.95, 7).quality, "medium")
        self.assertEqual(quality_for_xyz_finding(8, "day", 1.0, 7).quality, "low")

    def test_weekly_buckets_use_their_own_ladder(self) -> None:
        self.assertEqual(quality_for_xyz_finding(30, "week", 1.0, 4).quality, "high")
        self.assertEqual(quality_for_xyz_finding(14, "week", 0.95, 4).quality, "medium")

    def test_a_poor_timestamp_rate_blocks_high(self) -> None:
        self.assertEqual(quality_for_xyz_finding(200, "day", 0.5, 7).quality, "low")

    def test_below_the_configured_minimum_is_insufficient(self) -> None:
        self.assertEqual(quality_for_xyz_finding(3, "day", 1.0, 7).quality, "insufficient")


class AffinityEvidenceTest(unittest.TestCase):
    def test_levels(self) -> None:
        self.assertEqual(quality_for_affinity_finding(500, 60, 0.12, 2).quality, "high")
        self.assertEqual(quality_for_affinity_finding(80, 15, 0.19, 2).quality, "medium")
        self.assertEqual(quality_for_affinity_finding(20, 3, 0.15, 2).quality, "low")
        self.assertEqual(quality_for_affinity_finding(20, 1, 0.05, 2).quality, "insufficient")

    def test_a_rare_pair_caps_at_low(self) -> None:
        assessment = quality_for_affinity_finding(5000, 60, 0.005, 2)
        self.assertEqual(assessment.quality, "low")
        self.assertTrue(any("support" in reason for reason in assessment.caps_applied))

    def test_two_co_picks_in_fifteen_orders_are_not_strong_evidence(self) -> None:
        self.assertEqual(quality_for_affinity_finding(15, 2, 0.133, 2).quality, "low")


class SimulationEvidenceTest(unittest.TestCase):
    def test_an_unverified_scenario_never_reaches_high(self) -> None:
        assessment = quality_for_simulation_finding(
            coverage_percent=100.0,
            comparable_orders=500,
            affected_orders=400,
            constraints_complete=True,
            sequence_basis="scan_confirmed",
            site_verified=False,
        )
        self.assertEqual(assessment.quality, "medium")
        self.assertTrue(any("site" in reason for reason in assessment.caps_applied))

    def test_site_verification_unlocks_high(self) -> None:
        assessment = quality_for_simulation_finding(
            coverage_percent=100.0,
            comparable_orders=500,
            affected_orders=400,
            constraints_complete=True,
            sequence_basis="scan_confirmed",
            site_verified=True,
        )
        self.assertEqual(assessment.quality, "high")

    def test_incomplete_constraints_cap_at_low(self) -> None:
        assessment = quality_for_simulation_finding(
            100.0, 500, 400, constraints_complete=False, sequence_basis="scan_confirmed"
        )
        self.assertEqual(assessment.quality, "low")

    def test_an_unknown_sequence_caps_at_low(self) -> None:
        assessment = quality_for_simulation_finding(
            100.0, 500, 400, constraints_complete=True, sequence_basis="unknown"
        )
        self.assertEqual(assessment.quality, "low")

    def test_no_affected_order_is_insufficient(self) -> None:
        assessment = quality_for_simulation_finding(
            100.0, 500, 0, constraints_complete=True, sequence_basis="scan_confirmed"
        )
        self.assertEqual(assessment.quality, "insufficient")


class ConstraintEvidenceTest(unittest.TestCase):
    def test_complete_but_unverified_data_caps_at_medium(self) -> None:
        assessment = quality_for_constraint_finding(9, 0, site_verified=False)
        self.assertEqual(assessment.quality, "medium")
        self.assertTrue(any("site" in reason for reason in assessment.caps_applied))

    def test_site_verification_unlocks_high(self) -> None:
        self.assertEqual(quality_for_constraint_finding(9, 0, site_verified=True).quality, "high")

    def test_unknown_values_lower_the_level(self) -> None:
        self.assertEqual(quality_for_constraint_finding(9, 5, site_verified=True).quality, "low")

    def test_no_check_evaluated_is_insufficient(self) -> None:
        self.assertEqual(quality_for_constraint_finding(0, 0).quality, "insufficient")

    def test_rejected_candidates_are_no_longer_hardcoded_to_high(self) -> None:
        analysis = analyse(example_dataset()).analysis
        constraint_findings = [
            f
            for f in analysis["findings"]
            if any(e.get("type") == "rejected_candidate" for e in f["evidence"])
        ]
        self.assertTrue(constraint_findings)
        for finding in constraint_findings:
            self.assertNotEqual(finding["evidence_quality"], "high")


class ThresholdsTest(unittest.TestCase):
    def test_defaults_are_returned_without_config(self) -> None:
        self.assertEqual(thresholds_for(None), DEFAULT_EVIDENCE_THRESHOLDS)

    def test_a_dataset_can_override_a_threshold(self) -> None:
        resolved = thresholds_for({"evidence_thresholds": {"route_orders_high": 5}})
        self.assertEqual(resolved["route_orders_high"], 5.0)
        self.assertEqual(
            resolved["route_coverage_high"], DEFAULT_EVIDENCE_THRESHOLDS["route_coverage_high"]
        )

    def test_an_unknown_override_key_is_ignored(self) -> None:
        resolved = thresholds_for({"evidence_thresholds": {"not_a_threshold": 1}})
        self.assertNotIn("not_a_threshold", resolved)

    def test_overridden_thresholds_change_the_reported_level(self) -> None:
        document = minimal_document()
        document["config"]["evidence_thresholds"] = {
            "route_orders_high": 1,
            "route_coverage_high": 1.0,
        }
        document["meta"]["sequence_basis"] = "scan_confirmed"
        analysis = analyse(Dataset.from_dict(document)).analysis
        first = analysis["findings"][0]
        self.assertEqual(first["evidence_quality"], "high")
        self.assertEqual(analysis["thresholds"]["evidence"]["route_orders_high"], 1.0)

    def test_the_thresholds_in_force_are_published(self) -> None:
        analysis = analyse(example_dataset()).analysis
        self.assertIn("evidence", analysis["thresholds"])
        self.assertIn("route_orders_high", analysis["thresholds"]["evidence"])


class PeriodDaysTest(unittest.TestCase):
    def test_inclusive_day_count(self) -> None:
        self.assertEqual(
            period_days("2026-03-02T00:00:00+01:00", "2026-03-09T23:59:59+01:00"), 8
        )

    def test_missing_period_returns_none(self) -> None:
        self.assertIsNone(period_days(None, "2026-03-09T00:00:00+01:00"))
        self.assertIsNone(period_days("not-a-date", "2026-03-09T00:00:00+01:00"))


class LevelOrderTest(unittest.TestCase):
    def test_the_ladder_is_ordered_worst_to_best(self) -> None:
        self.assertEqual(EVIDENCE_LEVELS, ("insufficient", "low", "medium", "high"))

    def test_evidence_quality_is_never_a_number(self) -> None:
        analysis = analyse(example_dataset()).analysis
        for finding in analysis["findings"]:
            self.assertIsInstance(finding["evidence_quality"], str)
            self.assertIn(finding["evidence_quality"], EVIDENCE_LEVELS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
