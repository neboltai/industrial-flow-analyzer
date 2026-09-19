"""Simulation replays history; it never writes to the source dataset."""

from __future__ import annotations

import copy
import unittest

from fixtures import example_dataset, minimal_document

from intralogistics_flow_analyzer.graph import build_graph
from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.routing import build_routes
from intralogistics_flow_analyzer.simulation import (
    SIMULATION_ASSUMPTIONS,
    SIMULATION_LIMITATIONS,
    Move,
    SimulationError,
    build_override,
    parse_moves,
    simulate_moves,
)


class SimulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = Dataset.from_dict(minimal_document())
        self.graph = build_graph(self.dataset.nodes, self.dataset.edges)

    def test_a_pair_swap_is_replayed_against_the_historical_orders(self) -> None:
        moves = [Move("pair_swap", "S1", "S2")]
        comparison = simulate_moves(self.dataset, self.graph, moves)
        self.assertEqual(comparison.baseline_distance_m, 120.0)
        self.assertEqual(comparison.simulated_distance_m, 100.0)
        self.assertEqual(comparison.estimated_reduction_m, 20.0)
        self.assertEqual(comparison.affected_order_ids, ["O2"])

    def test_the_source_dataset_is_never_mutated(self) -> None:
        before = copy.deepcopy(self.dataset.to_dict())
        simulate_moves(self.dataset, self.graph, [Move("pair_swap", "S1", "S2")])
        self.assertEqual(self.dataset.to_dict(), before)

    def test_the_override_swaps_exactly_two_locations(self) -> None:
        override = build_override(self.dataset, [Move("pair_swap", "S1", "S2")])
        self.assertEqual(override["S1"], "L1")
        self.assertEqual(override["S2"], "L2")
        self.assertEqual(override["S3"], "L3")

    def test_an_unsupported_move_type_is_refused(self) -> None:
        with self.assertRaises(SimulationError):
            parse_moves({"moves": [{"type": "relocate", "sku": "S1", "to_location": "L1"}]})

    def test_a_move_needs_two_different_skus(self) -> None:
        with self.assertRaises(SimulationError):
            parse_moves({"moves": [{"type": "pair_swap", "sku_a": "S1", "sku_b": "S1"}]})

    def test_a_move_on_an_unassigned_sku_is_refused(self) -> None:
        with self.assertRaises(SimulationError):
            build_override(self.dataset, [Move("pair_swap", "S1", "S_MISSING")])

    def test_the_same_sku_cannot_be_moved_twice_in_one_simulation(self) -> None:
        moves = [Move("pair_swap", "S1", "S2"), Move("pair_swap", "S1", "S3")]
        with self.assertRaises(SimulationError):
            build_override(self.dataset, moves)

    def test_an_empty_move_list_is_refused(self) -> None:
        with self.assertRaises(SimulationError):
            parse_moves({"moves": []})

    def test_constraint_status_travels_with_the_comparison(self) -> None:
        document = minimal_document()
        document["locations"][0]["fixed"] = True
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        comparison = simulate_moves(dataset, graph, [Move("pair_swap", "S1", "S2")])
        self.assertEqual(comparison.constraint_status, "constraint_violation")
        self.assertEqual(comparison.constraint_checks["fixed"], "fail")

    def test_assumptions_and_limitations_are_always_attached(self) -> None:
        comparison = simulate_moves(self.dataset, self.graph, [Move("pair_swap", "S1", "S2")])
        document = comparison.to_dict()
        self.assertEqual(document["assumptions"], SIMULATION_ASSUMPTIONS)
        self.assertEqual(document["limitations"], SIMULATION_LIMITATIONS)
        self.assertIn("Historical pick sequence remains unchanged.", document["assumptions"])

    def test_only_orders_routable_in_both_scenarios_are_compared(self) -> None:
        document = minimal_document()
        document["picks"][3]["pick_sequence"] = None
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        comparison = simulate_moves(dataset, graph, [Move("pair_swap", "S1", "S2")])
        self.assertEqual(comparison.baseline_orders, 2)
        self.assertEqual(comparison.simulated_orders, 2)

    def test_simulating_the_recommended_move_on_the_example_reproduces_the_figure(self) -> None:
        dataset = example_dataset()
        graph = build_graph(dataset.nodes, dataset.edges)
        baseline = build_routes(dataset, graph)
        comparison = simulate_moves(
            dataset, graph, [Move("pair_swap", "SKU-1001", "SKU-1004")], baseline
        )
        self.assertGreater(comparison.estimated_reduction_m, 0.0)
        self.assertEqual(comparison.constraint_status, "ok")
        self.assertEqual(
            round(comparison.baseline_distance_m - comparison.simulated_distance_m, 6),
            round(comparison.estimated_reduction_m, 6),
        )

    def test_parse_moves_accepts_a_bare_list(self) -> None:
        moves = parse_moves([{"type": "pair_swap", "sku_a": "S1", "sku_b": "S2"}])
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].sku_a, "S1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
