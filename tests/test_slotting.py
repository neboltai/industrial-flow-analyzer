"""Candidate generation, constraint gating and ranking of pair swaps."""

from __future__ import annotations

import unittest

from fixtures import example_dataset, minimal_dataset, minimal_document, mutate

from intralogistics_flow_analyzer.graph import build_graph
from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.routing import build_routes
from intralogistics_flow_analyzer.slotting import generate_candidates, recommend


def run(document: dict, top: int = 10):
    dataset = Dataset.from_dict(document)
    graph = build_graph(dataset.nodes, dataset.edges)
    routing = build_routes(dataset, graph)
    return dataset, recommend(dataset, graph, routing, top)


class SlottingTest(unittest.TestCase):
    def test_candidates_only_move_frequent_items_closer(self) -> None:
        dataset = minimal_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        candidates = generate_candidates(dataset, graph)
        self.assertIn(("S1", "S2"), candidates)
        self.assertNotIn(("S2", "S1"), candidates)

    def test_a_valid_swap_reduces_the_simulated_distance(self) -> None:
        _, result = run(minimal_document())
        self.assertEqual(len(result.recommendations), 1)
        recommendation = result.recommendations[0]
        self.assertEqual(recommendation["recommendation_id"], "SWAP-001")
        self.assertEqual(recommendation["sku_a"], "S1")
        self.assertEqual(recommendation["from_a"], "L2")
        self.assertEqual(recommendation["to_a"], "L1")
        self.assertEqual(recommendation["baseline_distance_m"], 120.0)
        self.assertEqual(recommendation["simulated_distance_m"], 100.0)
        self.assertEqual(recommendation["estimated_reduction_m"], 20.0)
        self.assertAlmostEqual(recommendation["estimated_reduction_percent"], 16.6667, places=3)

    def test_a_recommendation_names_the_orders_it_changed(self) -> None:
        _, result = run(minimal_document())
        recommendation = result.recommendations[0]
        self.assertEqual(recommendation["affected_orders"], 1)
        self.assertEqual(recommendation["affected_order_ids"], ["O2"])

    def test_the_measure_is_an_estimate_never_a_saving(self) -> None:
        _, result = run(minimal_document())
        recommendation = result.recommendations[0]
        self.assertEqual(recommendation["measure"], "estimated pick-distance reduction")
        blob = str(result.to_dict()).lower()
        self.assertNotIn("guaranteed", blob)
        # "saving" may only appear inside the explicit denial that one was computed.
        cursor = blob.find("saving")
        while cursor != -1:
            self.assertIn("not a realised", blob[max(0, cursor - 40) : cursor])
            cursor = blob.find("saving", cursor + 1)

    def test_a_swap_without_material_gain_is_rejected(self) -> None:
        document = minimal_document()
        # Put both SKUs at the same distance from the start node.
        document = mutate(document, ["locations", 1, "access_node_id"], "N1")
        _, result = run(document)
        self.assertEqual(result.recommendations, [])

    def test_a_weight_incompatible_swap_never_becomes_a_recommendation(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_weight_kg"], 19.0)
        document = mutate(document, ["locations", 0, "max_unit_weight_kg"], 5.0)
        dataset, result = run(document)
        self.assertEqual(result.recommendations, [])
        rejected = result.rejected_candidates[0]
        self.assertEqual(rejected["status"], "constraint_violation")
        self.assertIn("unit_weight", rejected["rejected_because"])

    def test_a_temperature_incompatible_swap_never_becomes_a_recommendation(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "temperature_zone"], "chilled")
        _, result = run(document)
        self.assertEqual(result.recommendations, [])
        self.assertIn(
            "temperature", result.rejected_candidates[0]["rejected_because"]
        )

    def test_a_fixed_location_is_never_proposed(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "fixed"], True)
        _, result = run(document)
        self.assertEqual(result.recommendations, [])
        self.assertIn("fixed", result.rejected_candidates[0]["rejected_because"])

    def test_missing_constraint_data_yields_insufficient_constraint_data(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_volume_m3"], None)
        _, result = run(document)
        self.assertEqual(result.recommendations, [])
        self.assertEqual(
            result.rejected_candidates[0]["status"], "insufficient_constraint_data"
        )

    def test_each_sku_is_moved_at_most_once_per_run(self) -> None:
        dataset = example_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        routing = build_routes(dataset, graph)
        result = recommend(dataset, graph, routing, 10)
        moved = [sku for r in result.recommendations for sku in (r["sku_a"], r["sku_b"])]
        self.assertEqual(len(moved), len(set(moved)))

    def test_maximum_recommendations_is_respected(self) -> None:
        dataset = example_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        routing = build_routes(dataset, graph)
        result = recommend(dataset, graph, routing, 0)
        self.assertEqual(result.recommendations, [])

    def test_example_dataset_produces_the_expected_swap(self) -> None:
        dataset = example_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        routing = build_routes(dataset, graph)
        result = recommend(dataset, graph, routing, 10)
        self.assertEqual(len(result.recommendations), 1)
        recommendation = result.recommendations[0]
        self.assertEqual({recommendation["sku_a"], recommendation["sku_b"]}, {"SKU-1001", "SKU-1004"})
        self.assertGreater(recommendation["estimated_reduction_m"], 0)
        for status in recommendation["constraint_checks"].values():
            self.assertEqual(status, "pass")

    def test_example_dataset_rejects_every_forbidden_family(self) -> None:
        dataset = example_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        routing = build_routes(dataset, graph)
        result = recommend(dataset, graph, routing, 10)
        reasons = {
            reason
            for row in result.rejected_candidates
            for reason in (row.get("rejected_because") or [])
        }
        for expected in ("unit_weight", "temperature", "fixed", "hazard"):
            self.assertIn(expected, reasons)
        statuses = {row["status"] for row in result.rejected_candidates}
        self.assertIn("no_material_improvement", statuses)
        self.assertIn("constraint_violation", statuses)

    def test_the_moves_document_matches_the_recommendations(self) -> None:
        _, result = run(minimal_document())
        moves = result.moves_document()
        self.assertEqual(len(moves["moves"]), 1)
        self.assertEqual(moves["moves"][0]["type"], "pair_swap")
        self.assertEqual(moves["moves"][0]["recommendation_id"], "SWAP-001")

    def test_candidate_generation_is_capped(self) -> None:
        dataset = example_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        candidates = generate_candidates(dataset, graph, maximum=3)
        self.assertEqual(len(candidates), 3)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
