"""The release builder and the schema validator, including their refusals.

A validator that never rejects anything proves nothing, so most of this module
feeds deliberately broken documents to the schemas and asserts that they are
caught.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from fixtures import ROOT, example_dataset, minimal_document

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_release  # noqa: E402
import validate_artifacts  # noqa: E402

try:
    import jsonschema  # noqa: F401

    JSONSCHEMA_AVAILABLE = True
except ImportError:  # pragma: no cover
    JSONSCHEMA_AVAILABLE = False

DATASET_SCHEMA = ROOT / "schemas" / "dataset.schema.json"
ANALYSIS_SCHEMA = ROOT / "schemas" / "analysis.schema.json"
RECOMMENDATIONS_SCHEMA = ROOT / "schemas" / "recommendations.schema.json"


class ReleaseArchiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.archive = build_release.build(Path(cls.tempdir.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def names(self):
        with zipfile.ZipFile(self.archive) as archive:
            return archive.namelist()

    def test_the_archive_verifies_clean(self) -> None:
        self.assertEqual(build_release.verify(self.archive), [])

    def test_the_archive_carries_no_git_directory(self) -> None:
        for name in self.names():
            # The directory, not the prefix: .gitignore is shipped on purpose.
            self.assertNotIn(".git/", name, msg=name)
            self.assertNotIn(".git", Path(name).parts, msg=name)
        self.assertIn("intralogistics-flow-analyzer/.gitignore", self.names())

    def test_build_excludes_git_without_modifying_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__", "*.egg-info"))
            for directory in (root / ".git", root / "src" / ".git"):
                directory.mkdir()
                (directory / "config").write_bytes(b"synthetic git sentinel\n")
            before = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("config")}
            archive = build_release.build(Path(temporary) / "output", root=root)
            with zipfile.ZipFile(archive) as zipped:
                self.assertFalse(any(".git" in Path(n).parts for n in zipped.namelist()))
            after = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("config")}
            self.assertEqual(before, after)

    def test_the_archive_carries_no_python_cache(self) -> None:
        for name in self.names():
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith((".pyc", ".pyo", ".pyd")))

    def test_the_archive_carries_no_secret(self) -> None:
        problems = [p for p in build_release.verify(self.archive) if "secret" in p]
        self.assertEqual(problems, [])

    def test_the_archive_excludes_build_and_dist_directories(self) -> None:
        for name in self.names():
            inner = name.split("/", 1)[1]
            self.assertFalse(inner.startswith("dist/"), msg=name)
            self.assertFalse(inner.startswith("build/"), msg=name)
            self.assertFalse(inner.startswith(".github/"), msg=name)

    def test_the_archive_has_a_single_named_root(self) -> None:
        roots = {name.split("/", 1)[0] for name in self.names()}
        self.assertEqual(roots, {"intralogistics-flow-analyzer"})

    def test_the_archive_contains_what_a_user_needs(self) -> None:
        names = set(self.names())
        for required in (
            "intralogistics-flow-analyzer/README.md",
            "intralogistics-flow-analyzer/LICENSE",
            "intralogistics-flow-analyzer/pyproject.toml",
            "intralogistics-flow-analyzer/.mcp.json",
            "intralogistics-flow-analyzer/mcp/server.py",
            "intralogistics-flow-analyzer/scripts/build_release.py",
            "intralogistics-flow-analyzer/src/intralogistics_flow_analyzer/cli.py",
            "intralogistics-flow-analyzer/tests/test_slotting.py",
            "intralogistics-flow-analyzer/skills/intralogistics-flow-analyzer/SKILL.md",
        ):
            self.assertIn(required, names)

    def test_the_build_is_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            second = build_release.build(Path(other))
            self.assertEqual(
                build_release.digest(self.archive), build_release.digest(second)
            )

    def test_the_archive_version_matches_the_engine(self) -> None:
        from intralogistics_flow_analyzer.models import ENGINE_VERSION

        self.assertEqual(build_release.read_version(), ENGINE_VERSION)
        self.assertIn(ENGINE_VERSION, self.archive.name)

    def test_verification_rejects_an_archive_containing_git(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bad = Path(temporary) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("intralogistics-flow-analyzer/README.md", "readme")
                archive.writestr("intralogistics-flow-analyzer/.git/config", "[core]")
            problems = build_release.verify(bad)
            self.assertTrue(any(".git" in p for p in problems), msg=problems)

    def test_verification_rejects_an_archive_containing_a_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bad = Path(temporary) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("intralogistics-flow-analyzer/README.md", "readme")
                archive.writestr("intralogistics-flow-analyzer/src/__pycache__/x.pyc", "x")
            problems = build_release.verify(bad)
            self.assertTrue(any("cache" in p.lower() for p in problems), msg=problems)

    def test_verification_rejects_an_archive_containing_a_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bad = Path(temporary) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("intralogistics-flow-analyzer/README.md", "readme")
                # Assembled at runtime: a literal here would (correctly) make
                # the scanner flag this very test file.
                fake = "_".join(("api", "key")) + ' = "' + "z" * 30 + '"\n'
                archive.writestr("intralogistics-flow-analyzer/config.py", fake)
            problems = build_release.verify(bad)
            self.assertTrue(any("secret" in p for p in problems), msg=problems)


@unittest.skipUnless(JSONSCHEMA_AVAILABLE, "jsonschema is not installed")
class SchemaValidationTest(unittest.TestCase):
    def valid_analysis(self):
        from intralogistics_flow_analyzer.reporting import analyse

        return analyse(example_dataset()).analysis

    # ------------------------------------------------------------- positive
    def test_the_committed_example_dataset_is_valid(self) -> None:
        document = json.loads((ROOT / "examples" / "fictional-small-warehouse" / "dataset.json").read_text())
        self.assertEqual(
            validate_artifacts.validate_document(document, DATASET_SCHEMA, "example"), []
        )

    def test_a_generated_analysis_is_valid(self) -> None:
        self.assertEqual(
            validate_artifacts.validate_document(
                self.valid_analysis(), ANALYSIS_SCHEMA, "analysis"
            ),
            [],
        )

    def test_a_dataset_without_the_new_meta_fields_is_still_valid(self) -> None:
        document = minimal_document()
        document["meta"].pop("sequence_basis", None)
        document["meta"].pop("distance_basis", None)
        self.assertEqual(
            validate_artifacts.validate_document(document, DATASET_SCHEMA, "legacy"), []
        )

    # ------------------------------------------------------------- negative
    def test_an_unknown_status_is_rejected(self) -> None:
        document = self.valid_analysis()
        document["status"] = "probably_fine"
        problems = validate_artifacts.validate_document(document, ANALYSIS_SCHEMA, "analysis")
        self.assertTrue(problems)
        self.assertTrue(any("status" in p for p in problems))

    def test_a_recommendation_without_constraint_checks_is_rejected(self) -> None:
        document = json.loads(
            (ROOT / "schemas" / "recommendations.schema.json").read_text(encoding="utf-8")
        )
        self.assertIn("constraint_checks", document["$defs"]["recommendation"]["required"])
        payload = {
            "schema_version": "1.0",
            "engine_version": "0.1.2",
            "method": "pair swaps",
            "thresholds": {},
            "recommendations": [
                {
                    "recommendation_id": "SWAP-001",
                    "type": "pair_swap",
                    "sku_a": "A",
                    "from_a": "L1",
                    "to_a": "L2",
                    "sku_b": "B",
                    "from_b": "L2",
                    "to_b": "L1",
                    "baseline_distance_m": 10.0,
                    "simulated_distance_m": 9.0,
                    "estimated_reduction_m": 1.0,
                    "estimated_reduction_percent": 10.0,
                    "affected_orders": 1,
                    "assumptions": [],
                    "limitations": [],
                }
            ],
        }
        problems = validate_artifacts.validate_document(
            payload, RECOMMENDATIONS_SCHEMA, "recommendations"
        )
        self.assertTrue(any("constraint_checks" in p for p in problems), msg=problems)

    def test_a_finding_without_claim_type_is_rejected(self) -> None:
        document = self.valid_analysis()
        document["findings"][0].pop("claim_type")
        problems = validate_artifacts.validate_document(document, ANALYSIS_SCHEMA, "analysis")
        self.assertTrue(any("claim_type" in p for p in problems), msg=problems)

    def test_a_finding_without_evidence_assessment_is_rejected(self) -> None:
        document = self.valid_analysis()
        document["findings"][0].pop("evidence_assessment")
        problems = validate_artifacts.validate_document(document, ANALYSIS_SCHEMA, "analysis")
        self.assertTrue(any("evidence_assessment" in p for p in problems), msg=problems)

    def test_an_unknown_sequence_basis_is_rejected(self) -> None:
        document = copy.deepcopy(minimal_document())
        document["meta"]["sequence_basis"] = "guessed"
        problems = validate_artifacts.validate_document(document, DATASET_SCHEMA, "dataset")
        self.assertTrue(any("sequence_basis" in p for p in problems), msg=problems)

    def test_an_unknown_distance_basis_is_rejected(self) -> None:
        document = copy.deepcopy(minimal_document())
        document["meta"]["distance_basis"] = "gps_trace"
        problems = validate_artifacts.validate_document(document, DATASET_SCHEMA, "dataset")
        self.assertTrue(any("distance_basis" in p for p in problems), msg=problems)

    def test_a_document_missing_a_required_field_is_rejected(self) -> None:
        document = copy.deepcopy(minimal_document())
        document.pop("locations")
        problems = validate_artifacts.validate_document(document, DATASET_SCHEMA, "dataset")
        self.assertTrue(any("locations" in p for p in problems), msg=problems)

    def test_an_unexpected_top_level_key_is_rejected(self) -> None:
        document = copy.deepcopy(minimal_document())
        document["surprise"] = True
        problems = validate_artifacts.validate_document(document, DATASET_SCHEMA, "dataset")
        self.assertTrue(problems, msg="dataset.schema.json should be a closed object")

    def test_every_schema_is_itself_a_valid_draft_2020_12_schema(self) -> None:
        from jsonschema import Draft202012Validator

        for path in (DATASET_SCHEMA, ANALYSIS_SCHEMA, RECOMMENDATIONS_SCHEMA):
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    def test_strict_structures_close_additional_properties(self) -> None:
        dataset_schema = json.loads(DATASET_SCHEMA.read_text(encoding="utf-8"))
        self.assertIs(dataset_schema["additionalProperties"], False)
        recommendations = json.loads(RECOMMENDATIONS_SCHEMA.read_text(encoding="utf-8"))
        self.assertIs(
            recommendations["$defs"]["constraint_checks"].get("additionalProperties") is False,
            False,
            msg="constraint_checks constrains its values, not its key set",
        )

    def test_open_structures_stay_open(self) -> None:
        analysis_schema = json.loads(ANALYSIS_SCHEMA.read_text(encoding="utf-8"))
        factors = analysis_schema["$defs"]["finding"]["properties"]["evidence_assessment"][
            "properties"
        ]["factors"]
        self.assertNotIn(
            "additionalProperties",
            factors,
            msg="evidence factors differ per finding family and must stay extensible",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
