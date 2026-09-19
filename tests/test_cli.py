"""End-to-end behaviour of the command line interface and exit codes."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import (
    EVAL_CASES_DIR,
    EXAMPLE_DATASET_PATH,
    EXAMPLE_MAPPING_PATH,
    EXAMPLE_RAW_DIR,
    minimal_document,
    mutate,
)

from intralogistics_flow_analyzer.cli import main


def run(argv) -> tuple:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class CliTest(unittest.TestCase):
    def test_normalize_then_validate_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "dataset.json"
            code, output = run(
                [
                    "normalize",
                    "--input",
                    str(EXAMPLE_RAW_DIR),
                    "--mapping",
                    str(EXAMPLE_MAPPING_PATH),
                    "--output",
                    str(target),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("picker_name", output)
            self.assertTrue(target.is_file())
            code, _ = run(["validate", "--dataset", str(target)])
            self.assertEqual(code, 0)

    def test_validate_returns_one_on_a_blocking_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            document = mutate(minimal_document(), ["picks", 0, "quantity"], -1)
            path.write_text(json.dumps(document), encoding="utf-8")
            code, output = run(["validate", "--dataset", str(path)])
            self.assertEqual(code, 1)
            self.assertIn("INVALID_QUANTITY", output)

    def test_validate_returns_two_on_a_partially_analysable_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "partial.json"
            document = mutate(minimal_document(), ["picks", 0, "pick_sequence"], None)
            path.write_text(json.dumps(document), encoding="utf-8")
            code, output = run(["validate", "--dataset", str(path)])
            self.assertEqual(code, 2)
            self.assertIn("MISSING_PICK_SEQUENCE", output)

    def test_analyze_writes_every_acceptance_artefact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "analysis"
            code, _ = run(
                [
                    "analyze",
                    "--dataset",
                    str(EXAMPLE_DATASET_PATH),
                    "--output",
                    str(output_dir),
                ]
            )
            self.assertEqual(code, 0)
            for name in (
                "analysis.json",
                "report.md",
                "report.html",
                "flow-map.svg",
                "spaghetti.svg",
            ):
                self.assertTrue((output_dir / name).is_file(), msg=name)

    def test_recommend_writes_csv_json_and_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "recommendations"
            code, output = run(
                [
                    "recommend",
                    "--dataset",
                    str(EXAMPLE_DATASET_PATH),
                    "--top",
                    "10",
                    "--output",
                    str(output_dir),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("estimated pick-distance reduction", output)
            for name in ("recommendations.json", "recommendations.csv", "moves.json"):
                self.assertTrue((output_dir / name).is_file(), msg=name)

    def test_simulate_consumes_the_moves_document_produced_by_recommend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recommendations = Path(tmp) / "rec"
            simulation = Path(tmp) / "sim"
            run(
                [
                    "recommend",
                    "--dataset",
                    str(EXAMPLE_DATASET_PATH),
                    "--top",
                    "10",
                    "--output",
                    str(recommendations),
                ]
            )
            code, output = run(
                [
                    "simulate",
                    "--dataset",
                    str(EXAMPLE_DATASET_PATH),
                    "--moves",
                    str(recommendations / "moves.json"),
                    "--output",
                    str(simulation),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("Estimated pick-distance reduction", output)
            document = json.loads((simulation / "simulation.json").read_text(encoding="utf-8"))
            self.assertEqual(document["constraint_status"], "ok")
            self.assertGreater(document["estimated_reduction_m"], 0)

    def test_run_evals_passes_on_the_shipped_cases(self) -> None:
        code, output = run(["run-evals", "--cases-dir", str(EVAL_CASES_DIR)])
        self.assertEqual(code, 0, msg=output)
        for category in (
            "validation",
            "routing",
            "metrics",
            "classification",
            "constraint handling",
            "unsupported claims",
            "overall",
        ):
            self.assertIn(category, output)

    def test_describe_summarises_without_analysing(self) -> None:
        code, output = run(["describe", "--dataset", str(EXAMPLE_DATASET_PATH)])
        self.assertEqual(code, 0)
        self.assertIn("fictional-small-warehouse", output)
        self.assertIn("pedestrian", output)

    def test_a_missing_file_fails_cleanly(self) -> None:
        code, output = run(["validate", "--dataset", "does-not-exist.json"])
        self.assertEqual(code, 1)
        self.assertIn("File not found", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
