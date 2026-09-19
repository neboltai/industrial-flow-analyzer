"""Co-pick affinity: counts, support, confidence, lift, and safe division."""

from __future__ import annotations

import unittest

from fixtures import example_dataset, minimal_document

from intralogistics_flow_analyzer.affinity import (
    AFFINITY_DISCLAIMER,
    compute_affinities,
)
from intralogistics_flow_analyzer.models import Dataset


class AffinityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = Dataset.from_dict(minimal_document())
        self.rows = compute_affinities(self.dataset)

    def test_co_occurrence_is_counted_per_order(self) -> None:
        row = next(r for r in self.rows if {r["sku_a"], r["sku_b"]} == {"S1", "S2"})
        self.assertEqual(row["co_pick_count"], 1)
        self.assertEqual(row["total_orders"], 3)

    def test_support_is_the_share_of_all_orders(self) -> None:
        row = next(r for r in self.rows if {r["sku_a"], r["sku_b"]} == {"S1", "S2"})
        self.assertAlmostEqual(row["support"], 1 / 3, places=6)

    def test_confidence_is_directional(self) -> None:
        row = next(r for r in self.rows if {r["sku_a"], r["sku_b"]} == {"S1", "S2"})
        self.assertAlmostEqual(row["confidence_a_to_b"], 0.5, places=6)
        self.assertAlmostEqual(row["confidence_b_to_a"], 1.0, places=6)

    def test_lift_matches_the_documented_formula(self) -> None:
        row = next(r for r in self.rows if {r["sku_a"], r["sku_b"]} == {"S1", "S2"})
        expected = (1 / 3) / ((2 / 3) * (1 / 3))
        self.assertAlmostEqual(row["lift"], expected, places=6)

    def test_no_division_by_zero_when_an_item_is_never_picked(self) -> None:
        document = minimal_document()
        document["items"].append(
            {
                "sku": "S9",
                "description": "Never picked",
                "unit_volume_m3": 0.001,
                "unit_weight_kg": 1.0,
                "temperature_zone": "ambient",
                "hazard_class": "none",
                "handling_mode": "pedestrian",
                "fixed_location": False,
            }
        )
        rows = compute_affinities(Dataset.from_dict(document))
        self.assertTrue(all(row["sku_a"] != "S9" and row["sku_b"] != "S9" for row in rows))

    def test_empty_dataset_returns_no_pairs_instead_of_failing(self) -> None:
        document = minimal_document()
        document["picks"] = []
        self.assertEqual(compute_affinities(Dataset.from_dict(document)), [])

    def test_thresholds_filter_the_result(self) -> None:
        rows = compute_affinities(self.dataset, minimum_co_picks=2)
        self.assertEqual(rows, [])

    def test_every_row_carries_the_non_causality_statement(self) -> None:
        for row in self.rows:
            self.assertEqual(row["interpretation"], AFFINITY_DISCLAIMER)

    def test_example_dataset_exposes_a_strong_pair(self) -> None:
        rows = compute_affinities(example_dataset())
        self.assertTrue(rows)
        top = rows[0]
        self.assertEqual({top["sku_a"], top["sku_b"]}, {"SKU-1001", "SKU-1002"})
        self.assertGreaterEqual(top["co_pick_count"], 5)
        self.assertIsNotNone(top["lift"])

    def test_pairs_are_ordered_by_evidence_then_lift(self) -> None:
        rows = compute_affinities(example_dataset())
        counts = [row["co_pick_count"] for row in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
