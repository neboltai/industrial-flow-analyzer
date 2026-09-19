"""Flow metrics, attribution and the documented percentile definition."""

from __future__ import annotations

import unittest

from fixtures import minimal_dataset, minimal_document

from intralogistics_flow_analyzer.graph import build_graph
from intralogistics_flow_analyzer.metrics import (
    compute_metrics,
    data_quality,
    edge_flows,
    location_metrics,
    percentile,
    sku_metrics,
    top_locations_by_distance,
    top_orders_by_distance,
    zone_flows,
)
from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.routing import build_routes


class MetricsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = minimal_dataset()
        self.graph = build_graph(self.dataset.nodes, self.dataset.edges)
        self.routing = build_routes(self.dataset, self.graph)
        self.metrics = compute_metrics(self.dataset, self.routing, self.graph)

    def test_total_distance(self) -> None:
        self.assertEqual(self.metrics["total_distance_m"], 120.0)

    def test_average_distance_per_order(self) -> None:
        self.assertEqual(self.metrics["average_distance_per_order_m"], 40.0)

    def test_median_distance_per_order(self) -> None:
        self.assertEqual(self.metrics["median_distance_per_order_m"], 40.0)

    def test_p90_distance_per_order(self) -> None:
        self.assertEqual(self.metrics["p90_distance_per_order_m"], 40.0)

    def test_average_distance_per_pick_line(self) -> None:
        self.assertEqual(self.metrics["average_distance_per_pick_m"], 30.0)

    def test_percentile_uses_linear_interpolation_between_closest_ranks(self) -> None:
        values = [10.0, 20.0, 30.0, 40.0]
        self.assertEqual(percentile(values, 0.0), 10.0)
        self.assertEqual(percentile(values, 100.0), 40.0)
        self.assertEqual(percentile(values, 50.0), 25.0)
        self.assertAlmostEqual(percentile(values, 90.0), 37.0)
        self.assertIsNone(percentile([], 50.0))
        self.assertEqual(percentile([7.0], 90.0), 7.0)

    def test_percentile_rejects_an_out_of_range_p(self) -> None:
        with self.assertRaises(ValueError):
            percentile([1.0, 2.0], 101.0)

    def test_location_distance_contribution_sums_to_the_total(self) -> None:
        rows = {row["location_id"]: row for row in location_metrics(self.dataset, self.routing, self.graph)}
        self.assertEqual(rows["L2"]["visits"], 2)
        self.assertEqual(rows["L2"]["attributable_distance_m"], 60.0)
        self.assertEqual(rows["L1"]["attributable_distance_m"], 20.0)
        self.assertEqual(rows["L3"]["attributable_distance_m"], 40.0)
        total = sum(row["attributable_distance_m"] for row in rows.values())
        self.assertEqual(total, self.metrics["total_distance_m"])

    def test_location_distance_from_start_node(self) -> None:
        rows = {row["location_id"]: row for row in location_metrics(self.dataset, self.routing, self.graph)}
        self.assertEqual(rows["L1"]["distance_from_start_node_m"], 10.0)
        self.assertEqual(rows["L2"]["distance_from_start_node_m"], 20.0)

    def test_edge_traversals_are_aggregated_over_all_routes(self) -> None:
        rows = {row["edge_id"]: row for row in edge_flows(self.dataset, self.routing)}
        self.assertEqual(rows["E1"]["traversals"], 6)
        self.assertEqual(rows["E2"]["traversals"], 4)
        self.assertEqual(rows["E3"]["traversals"], 2)
        self.assertEqual(rows["E1"]["travelled_distance_m"], 60.0)

    def test_zone_transitions_are_summed_over_routes(self) -> None:
        self.assertEqual(self.metrics["zone_transitions"], 6)

    def test_zone_flows_report_visit_shares(self) -> None:
        rows = {row["zone_id"]: row for row in zone_flows(self.dataset, self.routing)}
        self.assertEqual(rows["Z1"]["visits"], 3)
        self.assertEqual(rows["Z2"]["visits"], 1)

    def test_sku_metrics_report_lines_units_and_orders(self) -> None:
        rows = {row["sku"]: row for row in sku_metrics(self.dataset, self.routing, self.graph)}
        self.assertEqual(rows["S1"]["pick_lines"], 2)
        self.assertEqual(rows["S1"]["units"], 4.0)
        self.assertEqual(rows["S1"]["orders"], 2)
        self.assertEqual(rows["S1"]["pick_location_id"], "L2")

    def test_top_orders_and_locations_are_ranked_deterministically(self) -> None:
        orders = top_orders_by_distance(self.routing, 10)
        self.assertEqual([o["order_id"] for o in orders], ["O1", "O2", "O3"])
        rows = location_metrics(self.dataset, self.routing, self.graph)
        top = top_locations_by_distance(rows, 10)
        self.assertEqual(top[0]["location_id"], "L2")

    def test_excluded_orders_never_enter_a_distance_average(self) -> None:
        document = minimal_document()
        document["picks"][2]["pick_sequence"] = None
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        routing = build_routes(dataset, graph)
        metrics = compute_metrics(dataset, routing, graph)
        self.assertEqual(metrics["total_valid_orders"], 2)
        self.assertEqual(metrics["total_distance_m"], 80.0)
        self.assertEqual(metrics["unsequenced_order_count"], 1)
        self.assertEqual(metrics["average_distance_per_order_m"], 40.0)

    def test_data_quality_reports_route_coverage(self) -> None:
        quality = data_quality(self.dataset, self.routing)
        self.assertEqual(quality["orders_total"], 3)
        self.assertEqual(quality["orders_routed"], 3)
        self.assertEqual(quality["route_coverage_percent"], 100.0)
        self.assertEqual(quality["pick_locations_total"], 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
