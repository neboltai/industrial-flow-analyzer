"""Route reconstruction from the historical pick sequence.

Reference values for the minimal fixture (start and end node N0):

    O1  N0 -> L2(N2) -> L1(N1) -> N0 = 20 + 10 + 10 = 40 m
    O2  N0 -> L2(N2) -> N0        = 20 + 20        = 40 m
    O3  N0 -> L3(N3) -> N0        = 20 + 20        = 40 m
"""

from __future__ import annotations

import unittest

from fixtures import minimal_dataset, minimal_document

from intralogistics_flow_analyzer.graph import build_graph
from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.routing import build_routes


def routes_of(document) -> dict:
    dataset = Dataset.from_dict(document)
    graph = build_graph(dataset.nodes, dataset.edges)
    return build_routes(dataset, graph).by_order()


class RoutingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = minimal_dataset()
        self.graph = build_graph(self.dataset.nodes, self.dataset.edges)
        self.result = build_routes(self.dataset, self.graph)
        self.routes = self.result.by_order()

    def test_multi_line_order_distance(self) -> None:
        self.assertEqual(self.routes["O1"].total_distance_m, 40.0)
        self.assertEqual(self.routes["O1"].number_of_lines, 2)
        self.assertTrue(self.routes["O1"].complete)

    def test_single_line_order_distance(self) -> None:
        self.assertEqual(self.routes["O2"].total_distance_m, 40.0)

    def test_route_starts_and_ends_at_the_configured_nodes(self) -> None:
        route = self.routes["O1"]
        self.assertEqual(route.stop_nodes[0], "N0")
        self.assertEqual(route.stop_nodes[-1], "N0")
        self.assertEqual(route.stop_nodes, ["N0", "N2", "N1", "N0"])

    def test_distance_between_picks_is_reported_leg_by_leg(self) -> None:
        self.assertEqual(self.routes["O1"].distance_between_picks_m, [20.0, 10.0, 10.0])

    def test_edge_traversals_are_counted_per_route(self) -> None:
        self.assertEqual(self.routes["O1"].edge_traversals, {"E1": 2, "E2": 2})
        self.assertEqual(self.routes["O3"].edge_traversals, {"E1": 2, "E3": 2})

    def test_zone_transitions_are_counted_between_stops(self) -> None:
        self.assertEqual(self.routes["O1"].zone_transitions, 2)
        self.assertEqual(self.result.total_distance_m(), 120.0)

    def test_order_without_pick_sequence_is_not_routed(self) -> None:
        document = minimal_document()
        document["picks"][0]["pick_sequence"] = None
        routes = routes_of(document)
        self.assertFalse(routes["O1"].complete)
        self.assertEqual(routes["O1"].reason, "MISSING_PICK_SEQUENCE")
        self.assertIsNone(routes["O1"].total_distance_m)

    def test_file_order_is_never_used_as_a_sequence_substitute(self) -> None:
        document = minimal_document()
        document["picks"][0]["pick_sequence"] = 2
        document["picks"][1]["pick_sequence"] = 1
        routes = routes_of(document)
        # Same two stops in the opposite order: N0 -> N1 -> N2 -> N0 = 10 + 10 + 20.
        self.assertEqual(routes["O1"].stop_nodes, ["N0", "N1", "N2", "N0"])
        self.assertEqual(routes["O1"].total_distance_m, 40.0)

    def test_order_with_an_unreachable_segment_is_excluded(self) -> None:
        document = minimal_document()
        document["picks"][3]["mode"] = "forklift"
        routes = routes_of(document)
        self.assertFalse(routes["O3"].complete)
        self.assertEqual(routes["O3"].reason, "UNREACHABLE_SEGMENT")
        self.assertIsNone(routes["O3"].total_distance_m)
        self.assertTrue(routes["O3"].unreachable_segments)

    def test_mixed_travel_modes_inside_one_order_are_not_routed(self) -> None:
        document = minimal_document()
        document["picks"][0]["mode"] = "forklift"
        routes = routes_of(document)
        self.assertFalse(routes["O1"].complete)
        self.assertEqual(routes["O1"].reason, "MIXED_ORDER_MODES")

    def test_location_override_replays_history_against_a_new_slotting(self) -> None:
        overridden = build_routes(self.dataset, self.graph, location_override={"S1": "L1"})
        # S1 moves from L2 (20 m) to L1 (10 m); O2 becomes 10 + 10 = 20 m.
        self.assertEqual(overridden.by_order()["O2"].total_distance_m, 20.0)
        # The source dataset is untouched.
        self.assertEqual(self.dataset.pick_location_of()["S1"], "L2")

    def test_consecutive_picks_at_the_same_access_node_add_no_travel(self) -> None:
        document = minimal_document()
        document["assignments"][1]["location_id"] = "L2"
        document["assignments"][1]["sku"] = "S2"
        document["locations"].append(
            {
                "location_id": "L4",
                "access_node_id": "N2",
                "zone_id": "Z1",
                "location_type": "pick",
                "capacity_volume_m3": 1.0,
                "capacity_weight_kg": 200.0,
                "max_unit_weight_kg": 20.0,
                "temperature_zone": "ambient",
                "hazard_classes_allowed": ["none"],
                "allowed_modes": ["pedestrian"],
                "level": 1,
                "fixed": False,
            }
        )
        document["assignments"][1]["location_id"] = "L4"
        document["picks"][1]["location_id"] = "L4"
        routes = routes_of(document)
        self.assertEqual(routes["O1"].total_distance_m, 40.0)
        self.assertEqual(routes["O1"].distance_between_picks_m, [20.0, 0.0, 20.0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
