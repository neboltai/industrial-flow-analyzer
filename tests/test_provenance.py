"""`sequence_basis` and `distance_basis`: all four values, and their absence.

Both fields are additive in engine 0.1.1. A schema 1.0 document written before
they existed must keep loading, keep validating and keep producing the same
numbers; only the *wording* a report is allowed to use changes.
"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import (
    EXAMPLE_MAPPING_PATH,
    EXAMPLE_RAW_DIR,
    example_dataset,
    minimal_document,
    mutate,
)

from intralogistics_flow_analyzer.models import (
    DEFAULT_DISTANCE_BASIS,
    DEFAULT_SEQUENCE_BASIS,
    DISTANCE_BASES,
    Dataset,
    SEQUENCE_BASES,
)
from intralogistics_flow_analyzer.normalizer import (
    NormalizationQuestion,
    load_mapping,
    normalize,
)
from intralogistics_flow_analyzer.reporting import analyse, render_markdown
from intralogistics_flow_analyzer.validation import validate


def without_provenance() -> dict:
    """A dataset exactly as engine 0.1.0 would have written it."""
    document = minimal_document()
    document["meta"].pop("sequence_basis", None)
    document["meta"].pop("distance_basis", None)
    return document


class BackwardCompatibilityTest(unittest.TestCase):
    def test_a_historical_dataset_without_the_fields_still_loads(self) -> None:
        dataset = Dataset.from_dict(without_provenance())
        self.assertEqual(dataset.meta.sequence_basis, DEFAULT_SEQUENCE_BASIS)
        self.assertEqual(dataset.meta.distance_basis, DEFAULT_DISTANCE_BASIS)

    def test_a_historical_dataset_still_validates(self) -> None:
        report = validate(Dataset.from_dict(without_provenance()))
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.errors, [])

    def test_the_absence_is_reported_but_does_not_block(self) -> None:
        report = validate(Dataset.from_dict(without_provenance()))
        codes = {issue.code: issue.severity for issue in report.issues}
        self.assertEqual(codes.get("SEQUENCE_BASIS_UNKNOWN"), "info")

    def test_a_historical_dataset_produces_identical_numbers(self) -> None:
        legacy = analyse(Dataset.from_dict(without_provenance())).analysis
        declared = analyse(
            Dataset.from_dict(mutate(minimal_document(), ["meta", "sequence_basis"], "observed"))
        ).analysis
        self.assertEqual(legacy["metrics"], declared["metrics"])

    def test_round_tripping_re_emits_the_defaults(self) -> None:
        dataset = Dataset.from_dict(without_provenance())
        document = dataset.to_dict()
        self.assertEqual(document["meta"]["sequence_basis"], "unknown")
        self.assertEqual(document["meta"]["distance_basis"], DEFAULT_DISTANCE_BASIS)


class SequenceBasisValueTest(unittest.TestCase):
    def test_all_four_declared_values_are_accepted(self) -> None:
        self.assertEqual(SEQUENCE_BASES, ("planned", "scan_confirmed", "observed", "unknown"))
        for value in SEQUENCE_BASES:
            document = mutate(minimal_document(), ["meta", "sequence_basis"], value)
            report = validate(Dataset.from_dict(document))
            self.assertEqual(report.status, "ok", msg=value)
            self.assertEqual(
                [i for i in report.errors], [], msg=f"{value} produced errors"
            )

    def test_planned_sequence_is_accepted_and_reported(self) -> None:
        document = mutate(minimal_document(), ["meta", "sequence_basis"], "planned")
        dataset = Dataset.from_dict(document)
        self.assertFalse(dataset.meta.sequence_is_confirmed())
        self.assertIn("may have differed", dataset.meta.sequence_basis_label())

    def test_scan_confirmed_supports_a_claim_about_what_was_picked(self) -> None:
        document = mutate(minimal_document(), ["meta", "sequence_basis"], "scan_confirmed")
        self.assertTrue(Dataset.from_dict(document).meta.sequence_is_confirmed())

    def test_observed_supports_a_claim_about_what_was_picked(self) -> None:
        document = mutate(minimal_document(), ["meta", "sequence_basis"], "observed")
        self.assertTrue(Dataset.from_dict(document).meta.sequence_is_confirmed())

    def test_unknown_supports_nothing_beyond_a_modelled_figure(self) -> None:
        document = mutate(minimal_document(), ["meta", "sequence_basis"], "unknown")
        self.assertFalse(Dataset.from_dict(document).meta.sequence_is_confirmed())

    def test_an_unsupported_value_is_a_blocking_error(self) -> None:
        document = mutate(minimal_document(), ["meta", "sequence_basis"], "vibes")
        report = validate(Dataset.from_dict(document))
        self.assertIn("UNKNOWN_SEQUENCE_BASIS", {i.code for i in report.issues})
        self.assertEqual(report.status, "data_error")

    def test_the_value_reaches_every_finding_that_depends_on_it(self) -> None:
        for value in ("planned", "scan_confirmed"):
            document = mutate(minimal_document(), ["meta", "sequence_basis"], value)
            analysis = analyse(Dataset.from_dict(document)).analysis
            route_finding = analysis["findings"][0]
            self.assertEqual(
                route_finding["evidence_assessment"]["factors"]["sequence_basis"], value
            )


class DistanceBasisValueTest(unittest.TestCase):
    def test_schema_one_supports_exactly_one_value(self) -> None:
        self.assertEqual(DISTANCE_BASES, ("declared_graph_shortest_path",))

    def test_an_unsupported_value_is_a_blocking_error(self) -> None:
        document = mutate(minimal_document(), ["meta", "distance_basis"], "gps_trace")
        report = validate(Dataset.from_dict(document))
        self.assertIn("UNKNOWN_DISTANCE_BASIS", {i.code for i in report.issues})
        self.assertEqual(report.status, "data_error")

    def test_numeric_field_names_are_unchanged(self) -> None:
        analysis = analyse(example_dataset()).analysis
        for key in ("total_distance_m", "median_distance_per_order_m", "p90_distance_per_order_m"):
            self.assertIn(key, analysis["metrics"])


class ReportWordingTest(unittest.TestCase):
    def test_the_report_states_both_bases(self) -> None:
        markdown = render_markdown(analyse(example_dataset()))
        self.assertIn("sequence_basis", markdown)
        self.assertIn("scan_confirmed", markdown)
        self.assertIn("distance_basis", markdown)
        self.assertIn("declared_graph_shortest_path", markdown)

    def test_the_thresholds_block_carries_the_provenance(self) -> None:
        analysis = analyse(example_dataset()).analysis
        self.assertEqual(analysis["thresholds"]["sequence_basis"], "scan_confirmed")
        self.assertEqual(
            analysis["thresholds"]["distance_basis"], "declared_graph_shortest_path"
        )

    def test_a_report_from_an_unknown_sequence_says_so(self) -> None:
        markdown = render_markdown(analyse(Dataset.from_dict(without_provenance())))
        self.assertIn("unknown", markdown)
        self.assertIn("not declared by the source system", markdown)


class NormalizerProvenanceTest(unittest.TestCase):
    def mapping(self) -> dict:
        import copy

        return copy.deepcopy(load_mapping(EXAMPLE_MAPPING_PATH))

    def test_the_example_mapping_declares_a_scan_confirmed_sequence(self) -> None:
        dataset = Dataset.from_dict(normalize(EXAMPLE_RAW_DIR, self.mapping()).dataset)
        self.assertEqual(dataset.meta.sequence_basis, "scan_confirmed")
        self.assertEqual(dataset.meta.distance_basis, "declared_graph_shortest_path")

    def test_an_absent_declaration_defaults_and_is_reported(self) -> None:
        document = self.mapping()
        document["meta"].pop("sequence_basis")
        result = normalize(EXAMPLE_RAW_DIR, document)
        self.assertEqual(result.dataset["meta"]["sequence_basis"], "unknown")
        self.assertIn("SEQUENCE_BASIS_DEFAULTED", {i.code for i in result.issues})

    def test_an_unsupported_declaration_raises_a_question(self) -> None:
        document = self.mapping()
        document["meta"]["sequence_basis"] = "probably-right"
        with self.assertRaises(NormalizationQuestion) as context:
            normalize(EXAMPLE_RAW_DIR, document)
        self.assertIn("sequence_basis", str(context.exception))

    def test_an_unsupported_distance_basis_raises_a_question(self) -> None:
        document = self.mapping()
        document["meta"]["distance_basis"] = "measured"
        with self.assertRaises(NormalizationQuestion):
            normalize(EXAMPLE_RAW_DIR, document)


class GermanPersonalColumnTest(unittest.TestCase):
    GERMAN_COLUMNS = (
        "mitarbeiter_id",
        "mitarbeiter_name",
        "personalnummer",
        "kommissionierer",
        "kommissionierer_name",
        "bediener_id",
        "bediener_name",
    )

    def _raw_with(self, column: str) -> Path:
        temporary = tempfile.mkdtemp()
        target = Path(temporary)
        for name in (
            "nodes.csv",
            "edges.csv",
            "locations.csv",
            "items.csv",
            "assignments.csv",
            "picks.csv",
        ):
            (target / name).write_text(
                (EXAMPLE_RAW_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
            )
        rows = list(csv.reader((target / "picks.csv").read_text(encoding="utf-8").splitlines()))
        rows[0].append(column)
        for row in rows[1:]:
            row.append("not-collected")
        with (target / "picks.csv").open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
        return target

    def mapping(self) -> dict:
        import copy

        return copy.deepcopy(load_mapping(EXAMPLE_MAPPING_PATH))

    def test_every_german_variant_is_dropped_and_reported(self) -> None:
        for column in self.GERMAN_COLUMNS:
            raw = self._raw_with(column)
            result = normalize(raw, self.mapping())
            self.assertIn(column, result.dropped_columns, msg=column)
            self.assertIn(
                "PERSONAL_COLUMN_DROPPED", {i.code for i in result.issues}, msg=column
            )
            self.assertNotIn(column, json.dumps(result.dataset).lower(), msg=column)

    def test_mapping_a_german_worker_column_is_refused(self) -> None:
        raw = self._raw_with("personalnummer")
        document = self.mapping()
        document["blocks"]["picks"]["columns"]["mode"] = {"source": "personalnummer"}
        with self.assertRaises(Exception) as context:
            normalize(raw, document)
        self.assertIn("worker-identifying", str(context.exception))

    def test_a_column_that_only_looks_personal_is_reported_not_silently_removed(self) -> None:
        raw = self._raw_with("mitarbeiter_kommentar")
        result = normalize(raw, self.mapping())
        codes = {i.code for i in result.issues}
        self.assertIn("SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED", codes)
        message = next(
            i.message for i in result.issues if i.code == "SUSPECTED_PERSONAL_COLUMN_NOT_MAPPED"
        )
        self.assertIn("mitarbeiter_kommentar", message)
        self.assertNotIn("mitarbeiter_kommentar", json.dumps(result.dataset).lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
