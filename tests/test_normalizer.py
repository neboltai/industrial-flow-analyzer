"""Normalisation refuses to guess, and never carries worker data forward."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import EXAMPLE_MAPPING_PATH, EXAMPLE_RAW_DIR, example_dataset

from intralogistics_flow_analyzer.models import Dataset
from intralogistics_flow_analyzer.normalizer import (
    NormalizationQuestion,
    load_mapping,
    normalize,
)
from intralogistics_flow_analyzer.validation import validate


def mapping() -> dict:
    return copy.deepcopy(load_mapping(EXAMPLE_MAPPING_PATH))


class NormalizerTest(unittest.TestCase):
    def test_the_example_mapping_produces_a_valid_dataset(self) -> None:
        result = normalize(EXAMPLE_RAW_DIR, mapping())
        dataset = Dataset.from_dict(result.dataset)
        self.assertEqual(validate(dataset).status, "ok")
        self.assertEqual(len(dataset.nodes), 12)
        self.assertEqual(len(dataset.edges), 16)
        self.assertEqual(len(dataset.locations), 12)
        self.assertEqual(len(dataset.items), 8)
        self.assertEqual(len(dataset.picks), 54)

    def test_normalisation_is_byte_for_byte_reproducible(self) -> None:
        first = json.dumps(normalize(EXAMPLE_RAW_DIR, mapping()).dataset, sort_keys=False)
        second = json.dumps(normalize(EXAMPLE_RAW_DIR, mapping()).dataset, sort_keys=False)
        self.assertEqual(first, second)

    def test_the_shipped_dataset_matches_a_fresh_normalisation(self) -> None:
        produced = normalize(EXAMPLE_RAW_DIR, mapping()).dataset
        self.assertEqual(Dataset.from_dict(produced).to_dict(), example_dataset().to_dict())

    def test_worker_columns_are_dropped_and_reported(self) -> None:
        result = normalize(EXAMPLE_RAW_DIR, mapping())
        self.assertIn("picker_name", result.dropped_columns)
        codes = {issue.code for issue in result.issues}
        self.assertIn("PERSONAL_COLUMN_DROPPED", codes)
        self.assertNotIn("picker_name", json.dumps(result.dataset).lower())

    def test_mapping_a_worker_column_is_refused(self) -> None:
        document = mapping()
        document["blocks"]["picks"]["columns"]["mode"] = {"source": "picker_name"}
        with self.assertRaises(Exception) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("worker-identifying", str(context.exception))

    def test_a_missing_unit_declaration_raises_a_question(self) -> None:
        document = mapping()
        del document["blocks"]["edges"]["columns"]["distance_m"]["unit"]
        with self.assertRaises(NormalizationQuestion) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("unit", str(context.exception))

    def test_an_unknown_unit_raises_a_question(self) -> None:
        document = mapping()
        document["blocks"]["edges"]["columns"]["distance_m"]["unit"] = "furlong"
        with self.assertRaises(NormalizationQuestion):
            normalize(EXAMPLE_RAW_DIR, document)

    def test_a_column_that_does_not_exist_raises_a_question_listing_the_headers(self) -> None:
        document = mapping()
        document["blocks"]["items"]["columns"]["sku"] = {"source": "not_a_column"}
        with self.assertRaises(NormalizationQuestion) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("article_no", str(context.exception))

    def test_a_missing_required_field_raises_a_question(self) -> None:
        document = mapping()
        del document["blocks"]["picks"]["columns"]["pick_id"]
        with self.assertRaises(NormalizationQuestion) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("pick_id", str(context.exception))

    def test_a_missing_block_raises_a_question(self) -> None:
        document = mapping()
        del document["blocks"]["assignments"]
        with self.assertRaises(NormalizationQuestion) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("assignments", str(context.exception))

    def test_missing_route_endpoints_raise_a_question(self) -> None:
        document = mapping()
        document["config"]["route_start_node"] = ""
        with self.assertRaises(NormalizationQuestion) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("route_start_node", str(context.exception))

    def test_an_unknown_transform_is_refused(self) -> None:
        document = mapping()
        document["blocks"]["items"]["columns"]["sku"]["transforms"] = ["guess_the_unit"]
        with self.assertRaises(Exception) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("unknown transform", str(context.exception))

    def test_unit_conversion_is_applied_when_declared(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            for name in (
                "nodes.csv",
                "edges.csv",
                "locations.csv",
                "items.csv",
                "assignments.csv",
                "picks.csv",
            ):
                (raw / name).write_text(
                    (EXAMPLE_RAW_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
                )
            lines = (raw / "edges.csv").read_text(encoding="utf-8").splitlines()
            converted = [lines[0]]
            for line in lines[1:]:
                parts = line.split(",")
                parts[3] = str(float(parts[3]) * 100.0)
                converted.append(",".join(parts))
            (raw / "edges.csv").write_text("\n".join(converted) + "\n", encoding="utf-8")

            document = mapping()
            document["blocks"]["edges"]["columns"]["distance_m"]["unit"] = "cm"
            dataset = Dataset.from_dict(normalize(raw, document).dataset)
            self.assertEqual(dataset.edges[0].distance_m, 10.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
