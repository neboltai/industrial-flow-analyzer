"""ABC and XYZ classification rules, thresholds and refusal to classify."""

from __future__ import annotations

import unittest

from fixtures import example_dataset, minimal_document, mutate

from intralogistics_flow_analyzer.classification import (
    XYZ_NOT_COMPUTED,
    abc_classify,
    combined_classes,
    xyz_classify,
)
from intralogistics_flow_analyzer.models import Dataset


def _document_with_skewed_demand() -> dict:
    """S1 = 7 lines, S2 = 2 lines, S3 = 1 line over 10 days (total 10)."""
    document = minimal_document()
    picks = []
    counter = 0
    plan = [("S1", "L2", 7), ("S2", "L1", 2), ("S3", "L3", 1)]
    day = 1
    for sku, location, count in plan:
        for _ in range(count):
            counter += 1
            picks.append(
                {
                    "pick_id": f"P{counter}",
                    "order_id": f"O{counter}",
                    "pick_sequence": 1,
                    "timestamp": f"2026-01-{day:02d}T08:00:00+01:00",
                    "sku": sku,
                    "location_id": location,
                    "quantity": 1,
                    "mode": "pedestrian",
                }
            )
            day = day + 1 if day < 10 else 1
    document["picks"] = picks
    return document


class AbcTest(unittest.TestCase):
    def test_normal_classification_ranks_by_contribution(self) -> None:
        dataset = Dataset.from_dict(_document_with_skewed_demand())
        rows = {row["sku"]: row for row in abc_classify(dataset)}
        self.assertEqual(rows["S1"]["rank"], 1)
        self.assertEqual(rows["S1"]["basis_value"], 7.0)
        self.assertEqual(rows["S1"]["abc_class"], "A")
        self.assertEqual(rows["S2"]["abc_class"], "A")  # crosses the 80 % threshold
        self.assertEqual(rows["S3"]["abc_class"], "B")

    def test_ties_are_broken_deterministically_by_sku(self) -> None:
        dataset = Dataset.from_dict(minimal_document())
        rows = abc_classify(dataset)
        tied = [row["sku"] for row in rows if row["basis_value"] == 1.0]
        self.assertEqual(tied, sorted(tied))

    def test_thresholds_are_configurable(self) -> None:
        document = _document_with_skewed_demand()
        document["config"]["abc_thresholds"] = {"A": 0.5, "B": 0.9}
        dataset = Dataset.from_dict(document)
        rows = {row["sku"]: row for row in abc_classify(dataset)}
        self.assertEqual(rows["S1"]["abc_class"], "A")
        self.assertEqual(rows["S2"]["abc_class"], "B")
        self.assertEqual(rows["S3"]["abc_class"], "C")

    def test_units_basis_changes_the_ranking(self) -> None:
        document = minimal_document()
        document["config"]["abc_basis"] = "units"
        dataset = Dataset.from_dict(document)
        rows = {row["sku"]: row for row in abc_classify(dataset)}
        self.assertEqual(rows["S1"]["basis_value"], 4.0)
        self.assertEqual(rows["S2"]["basis_value"], 2.0)
        self.assertEqual(rows["S1"]["basis"], "units")

    def test_orders_basis_counts_distinct_orders(self) -> None:
        document = minimal_document()
        document["config"]["abc_basis"] = "orders"
        dataset = Dataset.from_dict(document)
        rows = {row["sku"]: row for row in abc_classify(dataset)}
        self.assertEqual(rows["S1"]["basis_value"], 2.0)
        self.assertEqual(rows["S2"]["basis_value"], 1.0)

    def test_cumulative_share_reaches_one_hundred_percent(self) -> None:
        dataset = Dataset.from_dict(_document_with_skewed_demand())
        rows = abc_classify(dataset)
        self.assertAlmostEqual(rows[-1]["cumulative_share_percent"], 100.0, places=6)


class XyzTest(unittest.TestCase):
    def test_insufficient_periods_returns_the_explicit_marker(self) -> None:
        document = mutate(minimal_document(), ["meta", "period_end"], "2026-01-03T23:59:59+01:00")
        document = mutate(document, ["picks", 2, "timestamp"], "2026-01-02T08:00:00+01:00")
        document = mutate(document, ["picks", 3, "timestamp"], "2026-01-03T08:00:00+01:00")
        rows, marker = xyz_classify(Dataset.from_dict(document))
        self.assertEqual(marker, XYZ_NOT_COMPUTED)
        self.assertEqual(rows, [])

    def test_missing_demand_is_not_silently_labelled_z(self) -> None:
        document = minimal_document()
        document["items"].append(
            {
                "sku": "S4",
                "description": "Never picked",
                "unit_volume_m3": 0.001,
                "unit_weight_kg": 1.0,
                "temperature_zone": "ambient",
                "hazard_class": "none",
                "handling_mode": "pedestrian",
                "fixed_location": False,
            }
        )
        rows, marker = xyz_classify(Dataset.from_dict(document))
        self.assertIsNone(marker)
        row = next(r for r in rows if r["sku"] == "S4")
        self.assertIsNone(row["xyz_class"])
        self.assertIsNone(row["coefficient_of_variation"])

    def test_zero_variance_series_is_class_x(self) -> None:
        document = minimal_document()
        document["picks"] = [
            {
                "pick_id": f"P{index}",
                "order_id": f"O{index}",
                "pick_sequence": 1,
                "timestamp": f"2026-01-{index:02d}T08:00:00+01:00",
                "sku": "S1",
                "location_id": "L2",
                "quantity": 1,
                "mode": "pedestrian",
            }
            for index in range(1, 11)
        ]
        rows, marker = xyz_classify(Dataset.from_dict(document))
        self.assertIsNone(marker)
        row = next(r for r in rows if r["sku"] == "S1")
        self.assertEqual(row["coefficient_of_variation"], 0.0)
        self.assertEqual(row["xyz_class"], "X")

    def test_bucket_count_covers_the_whole_declared_period(self) -> None:
        rows, marker = xyz_classify(Dataset.from_dict(minimal_document()))
        self.assertIsNone(marker)
        self.assertEqual(rows[0]["buckets"], 10)
        self.assertEqual(rows[0]["bucket_type"], "day")

    def test_weekly_buckets_are_supported(self) -> None:
        document = minimal_document()
        document["config"]["xyz_time_bucket"] = "week"
        document["config"]["xyz_minimum_buckets"] = 2
        rows, marker = xyz_classify(Dataset.from_dict(document))
        self.assertIsNone(marker)
        self.assertEqual(rows[0]["bucket_type"], "week")

    def test_example_dataset_produces_a_full_classification(self) -> None:
        dataset = example_dataset()
        abc_rows = abc_classify(dataset)
        xyz_rows, marker = xyz_classify(dataset)
        self.assertIsNone(marker)
        combined = combined_classes(abc_rows, xyz_rows)
        self.assertEqual(len(combined), len(dataset.items))
        for value in combined.values():
            self.assertIn(value[0], "ABC")
            self.assertIn(value[1], "XYZ")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
