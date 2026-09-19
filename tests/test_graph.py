"""Graph construction and Dijkstra shortest-path search."""

from __future__ import annotations

import unittest

from fixtures import minimal_dataset, minimal_document

from intralogistics_flow_analyzer.graph import build_graph
from intralogistics_flow_analyzer.models import Dataset


class GraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = minimal_dataset()
        self.graph = build_graph(self.dataset.nodes, self.dataset.edges)

    def test_simple_path_uses_declared_edge_distance(self) -> None:
        path = self.graph.shortest_path("N0", "N1", "pedestrian")
        self.assertIsNotNone(path)
        self.assertEqual(path.distance_m, 10.0)
        self.assertEqual(path.node_path, ("N0", "N1"))
        self.assertEqual(path.edge_path, ("E1",))

    def test_bidirectional_edge_is_traversable_in_both_directions(self) -> None:
        forward = self.graph.shortest_path("N1", "N2", "pedestrian")
        backward = self.graph.shortest_path("N2", "N1", "pedestrian")
        self.assertEqual(forward.distance_m, backward.distance_m)
        self.assertEqual(backward.edge_path, ("E2",))

    def test_unidirectional_edge_is_not_traversable_backwards(self) -> None:
        document = minimal_document()
        document["layout"]["edges"][1]["bidirectional"] = False
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        self.assertIsNotNone(graph.shortest_path("N1", "N2", "pedestrian"))
        self.assertIsNone(graph.shortest_path("N2", "N1", "pedestrian"))

    def test_forbidden_mode_blocks_an_edge(self) -> None:
        self.assertIsNotNone(self.graph.shortest_path("N1", "N3", "pedestrian"))
        self.assertIsNone(self.graph.shortest_path("N1", "N3", "forklift"))

    def test_shortest_of_two_alternatives_is_selected(self) -> None:
        document = minimal_document()
        document["layout"]["edges"].append(
            {
                "edge_id": "E4",
                "from_node": "N0",
                "to_node": "N2",
                "distance_m": 5.0,
                "bidirectional": True,
                "allowed_modes": ["pedestrian"],
            }
        )
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        path = graph.shortest_path("N0", "N2", "pedestrian")
        self.assertEqual(path.distance_m, 5.0)
        self.assertEqual(path.edge_path, ("E4",))

    def test_unreachable_target_returns_none(self) -> None:
        document = minimal_document()
        document["layout"]["nodes"].append(
            {"node_id": "N9", "x_m": 99.0, "y_m": 0.0, "node_type": "junction", "zone_id": "Z9"}
        )
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        self.assertIsNone(graph.shortest_path("N0", "N9", "pedestrian"))
        self.assertEqual(graph.unreachable_nodes("N0", "pedestrian"), ["N9"])

    def test_identical_start_and_end_costs_nothing(self) -> None:
        path = self.graph.shortest_path("N2", "N2", "pedestrian")
        self.assertEqual(path.distance_m, 0.0)
        self.assertEqual(path.edge_path, ())

    def test_single_source_tree_is_cached_per_source_and_mode(self) -> None:
        self.assertEqual(self.graph.cache_size(), 0)
        self.graph.shortest_path("N0", "N1", "pedestrian")
        self.graph.shortest_path("N0", "N2", "pedestrian")
        self.graph.shortest_path("N0", "N3", "pedestrian")
        self.assertEqual(self.graph.cache_size(), 1)
        self.graph.shortest_path("N0", "N2", "forklift")
        self.assertEqual(self.graph.cache_size(), 2)

    def test_zero_length_edges_are_not_added_to_the_graph(self) -> None:
        document = minimal_document()
        document["layout"]["edges"][0]["distance_m"] = 0.0
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        self.assertIsNone(graph.shortest_path("N0", "N1", "pedestrian"))

    def test_euclidean_distance_is_never_substituted_for_a_graph_distance(self) -> None:
        # N0 and N2 are 20 m apart in a straight line but 20 m along the aisle too;
        # lengthening the declared edge must change the result, proving the graph wins.
        document = minimal_document()
        document["layout"]["edges"][1]["distance_m"] = 100.0
        dataset = Dataset.from_dict(document)
        graph = build_graph(dataset.nodes, dataset.edges)
        self.assertEqual(graph.distance("N0", "N2", "pedestrian"), 110.0)

    def test_weakly_connected_components_ignore_direction(self) -> None:
        components = self.graph.weakly_connected_components()
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0], ["N0", "N1", "N2", "N3"])

    def test_modes_present_in_the_graph_are_listed(self) -> None:
        self.assertEqual(self.graph.modes(), ["forklift", "pedestrian"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
