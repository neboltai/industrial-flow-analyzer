"""Deterministic validation: every inconsistency is reported, never repaired."""

from __future__ import annotations

import unittest

from fixtures import example_dataset, minimal_dataset, minimal_document, mutate

from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.validation import (
    EXIT_BLOCKING,
    EXIT_OK,
    EXIT_PARTIAL,
    validate,
)


def codes(report) -> set:
    return {issue.code for issue in report.issues}


class ValidationTest(unittest.TestCase):
    def test_clean_minimal_dataset_is_ok(self) -> None:
        report = validate(minimal_dataset())
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.errors, [])
        self.assertEqual(report.exit_code(), EXIT_OK)

    def test_shipped_example_dataset_is_ok(self) -> None:
        report = validate(example_dataset())
        self.assertEqual(report.status, "ok", msg=[i.message for i in report.errors])
        self.assertEqual(report.exit_code(), EXIT_OK)

    def test_duplicate_identifier_is_reported(self) -> None:
        document = minimal_document()
        document["picks"].append(dict(document["picks"][0]))
        report = validate(Dataset.from_dict(document))
        self.assertIn("DUPLICATE_PICK_ID", codes(report))
        self.assertEqual(report.status, "data_error")
        self.assertEqual(report.exit_code(), EXIT_BLOCKING)

    def test_unknown_node_reference_is_reported(self) -> None:
        document = mutate(minimal_document(), ["layout", "edges", 0, "to_node"], "N_MISSING")
        report = validate(Dataset.from_dict(document))
        self.assertIn("UNKNOWN_NODE_REFERENCE", codes(report))
        self.assertEqual(report.status, "data_error")

    def test_negative_edge_distance_is_reported(self) -> None:
        document = mutate(minimal_document(), ["layout", "edges", 1, "distance_m"], -5.0)
        report = validate(Dataset.from_dict(document))
        self.assertIn("INVALID_EDGE_DISTANCE", codes(report))

    def test_disconnected_graph_is_reported(self) -> None:
        document = minimal_document()
        document["layout"]["nodes"].append(
            {"node_id": "N9", "x_m": 99.0, "y_m": 99.0, "node_type": "junction", "zone_id": "Z9"}
        )
        report = validate(Dataset.from_dict(document))
        self.assertIn("DISCONNECTED_GRAPH", codes(report))
        self.assertEqual(report.status, "data_error")

    def test_unknown_sku_in_picks_is_reported(self) -> None:
        document = mutate(minimal_document(), ["picks", 0, "sku"], "S_MISSING")
        report = validate(Dataset.from_dict(document))
        self.assertIn("UNKNOWN_SKU", codes(report))

    def test_unknown_location_in_picks_is_reported(self) -> None:
        document = mutate(minimal_document(), ["picks", 0, "location_id"], "L_MISSING")
        report = validate(Dataset.from_dict(document))
        self.assertIn("UNKNOWN_LOCATION", codes(report))

    def test_duplicate_pick_sequence_is_reported(self) -> None:
        document = mutate(minimal_document(), ["picks", 1, "pick_sequence"], 1)
        report = validate(Dataset.from_dict(document))
        self.assertIn("DUPLICATE_PICK_SEQUENCE", codes(report))

    def test_non_positive_quantity_is_reported(self) -> None:
        document = mutate(minimal_document(), ["picks", 0, "quantity"], 0)
        report = validate(Dataset.from_dict(document))
        self.assertIn("INVALID_QUANTITY", codes(report))

    def test_non_positive_capacity_is_reported(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "capacity_weight_kg"], -1.0)
        report = validate(Dataset.from_dict(document))
        self.assertIn("INVALID_CAPACITY", codes(report))

    def test_inconsistent_capacity_is_reported(self) -> None:
        document = mutate(minimal_document(), ["locations", 0, "max_unit_weight_kg"], 5000.0)
        report = validate(Dataset.from_dict(document))
        self.assertIn("INCONSISTENT_CAPACITY", codes(report))

    def test_item_too_heavy_for_its_location_is_reported(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "unit_weight_kg"], 999.0)
        report = validate(Dataset.from_dict(document))
        self.assertIn("ITEM_TOO_HEAVY_FOR_LOCATION", codes(report))

    def test_temperature_incompatibility_is_reported(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "temperature_zone"], "chilled")
        report = validate(Dataset.from_dict(document))
        self.assertIn("TEMPERATURE_INCOMPATIBLE", codes(report))

    def test_hazard_incompatibility_is_reported(self) -> None:
        document = mutate(minimal_document(), ["items", 0, "hazard_class"], "flammable")
        report = validate(Dataset.from_dict(document))
        self.assertIn("HAZARD_INCOMPATIBLE", codes(report))

    def test_forbidden_handling_mode_is_reported(self) -> None:
        document = mutate(minimal_document(), ["items", 2, "handling_mode"], "forklift")
        report = validate(Dataset.from_dict(document))
        self.assertIn("MODE_NOT_ALLOWED", codes(report))

    def test_multiple_pick_assignments_are_reported(self) -> None:
        document = minimal_document()
        document["assignments"].append(
            {"sku": "S1", "location_id": "L3", "role": "pick", "current_units": 1, "max_units": 5}
        )
        report = validate(Dataset.from_dict(document))
        self.assertIn("MULTIPLE_PICK_ASSIGNMENTS", codes(report))

    def test_timestamp_outside_declared_period_is_reported(self) -> None:
        document = mutate(minimal_document(), ["picks", 0, "timestamp"], "2027-05-05T08:00:00+01:00")
        report = validate(Dataset.from_dict(document))
        self.assertIn("TIMESTAMP_OUTSIDE_PERIOD", codes(report))

    def test_unknown_distance_unit_is_reported(self) -> None:
        document = mutate(minimal_document(), ["meta", "distance_unit"], "ft")
        report = validate(Dataset.from_dict(document))
        self.assertIn("UNKNOWN_DISTANCE_UNIT", codes(report))

    def test_missing_pick_sequence_degrades_to_partial_without_inventing_distance(self) -> None:
        document = mutate(minimal_document(), ["picks", 0, "pick_sequence"], None)
        report = validate(Dataset.from_dict(document))
        self.assertIn("MISSING_PICK_SEQUENCE", codes(report))
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.exit_code(), EXIT_PARTIAL)

    def test_unreachable_route_is_reported_and_status_is_partial(self) -> None:
        document = minimal_document()
        # A forklift order that must reach the pedestrian-only aisle node N3.
        document["picks"] = [
            {
                "pick_id": "P1",
                "order_id": "O1",
                "pick_sequence": 1,
                "timestamp": "2026-01-01T08:00:00+01:00",
                "sku": "S3",
                "location_id": "L3",
                "quantity": 1,
                "mode": "forklift",
            },
            {
                "pick_id": "P2",
                "order_id": "O2",
                "pick_sequence": 1,
                "timestamp": "2026-01-02T08:00:00+01:00",
                "sku": "S1",
                "location_id": "L2",
                "quantity": 1,
                "mode": "pedestrian",
            },
        ]
        report = validate(Dataset.from_dict(document))
        self.assertIn("DISCONNECTED_ROUTE", codes(report))
        self.assertEqual(report.status, "partial")

    def test_nothing_is_repaired_silently(self) -> None:
        document = mutate(minimal_document(), ["picks", 0, "quantity"], -3)
        dataset = Dataset.from_dict(document)
        validate(dataset)
        self.assertEqual(dataset.picks[0].quantity, -3.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
