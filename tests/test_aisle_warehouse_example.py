"""The aisle-warehouse example is independent of the historical 15/15 regression."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fixtures import ROOT
from intralogistics_flow_analyzer.loaders import load_dataset
from intralogistics_flow_analyzer.reporting import analyse

EXAMPLE = ROOT / "examples" / "fictional-aisle-warehouse"
HISTORICAL = ROOT / "examples" / "fictional-small-warehouse" / "dataset.json"


class AisleWarehouseExampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = load_dataset(EXAMPLE / "dataset.json")
        cls.result = analyse(cls.dataset)
        cls.metrics = cls.result.analysis["metrics"]
        cls.landing = json.loads((EXAMPLE / "landing-metrics.json").read_text(encoding="utf-8"))

    def test_the_example_is_not_the_historical_regression_dataset(self) -> None:
        historical = json.loads(HISTORICAL.read_text(encoding="utf-8"))
        self.assertNotEqual(self.dataset.meta.dataset_id, historical["meta"]["dataset_id"])
        self.assertNotEqual(self.metrics["total_distance_m"], 1560.0)
        self.assertNotEqual(self.metrics["median_distance_per_order_m"], 100.0)

    def test_every_order_is_reconstructed(self) -> None:
        self.assertEqual(self.result.analysis["status"], "ok")
        self.assertEqual(self.metrics["unreachable_order_count"], 0)
        self.assertEqual(self.metrics["total_valid_orders"], 18)
        self.assertEqual(self.metrics["total_valid_orders"], self.metrics["total_orders"])

    def test_edges_are_axis_aligned(self) -> None:
        nodes = self.dataset.nodes_by_id()
        for edge in self.dataset.edges:
            source = nodes[edge.from_node]
            target = nodes[edge.to_node]
            self.assertTrue(
                source.x_m == target.x_m or source.y_m == target.y_m,
                msg=f"{edge.edge_id} is diagonal",
            )
            declared = abs(source.x_m - target.x_m) + abs(source.y_m - target.y_m)
            self.assertAlmostEqual(edge.distance_m, declared, places=3)

    def test_landing_metrics_match_this_dataset(self) -> None:
        self.assertEqual(self.landing["dataset_id"], "fictional-aisle-warehouse")
        self.assertEqual(self.landing["orders_reconstructed"], self.metrics["total_valid_orders"])
        self.assertEqual(self.landing["total_distance_m"], self.metrics["total_distance_m"])
        self.assertEqual(
            self.landing["median_distance_per_order_m"],
            self.metrics["median_distance_per_order_m"],
        )
        self.assertEqual(
            self.landing["p90_distance_per_order_m"],
            self.metrics["p90_distance_per_order_m"],
        )
        self.assertEqual(self.landing["routes_drawn"], 8)
        svg = (EXAMPLE / "spaghetti-landing.svg").read_text(encoding="utf-8")
        self.assertIn("viewBox", svg)
        self.assertIn("<title>", svg)
        self.assertIn("<desc>", svg)
        for order_id in self.landing["routes_drawn_order_ids"]:
            self.assertIn(order_id, svg)
        self.assertNotIn("1560", svg)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
