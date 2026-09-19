#!/usr/bin/env python3
"""Validate the engine's JSON artefacts against their schemas, for real.

Until 0.1.1 the CI only checked that the schema files parsed as JSON, which
proves nothing about the documents they are supposed to govern. This script
validates actual artefacts with ``jsonschema`` under Draft 2020-12:

* the committed example dataset, against ``dataset.schema.json``;
* a dataset freshly produced by ``ifa normalize``, against the same schema;
* every dataset shipped to the demonstration MCP server;
* ``analysis.json`` produced by ``ifa analyze``, against ``analysis.schema.json``;
* the recommendations document produced by ``ifa recommend``, against
  ``recommendations.schema.json``;
* every eval case that embeds a complete dataset.

``jsonschema`` lives in the ``dev`` extra: the engine itself never needs it.

Usage::

    python scripts/validate_artifacts.py            # build artefacts, validate
    python scripts/validate_artifacts.py --keep DIR # keep them for inspection
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas"
EXAMPLE_DIR = REPO_ROOT / "examples" / "fictional-small-warehouse"
EVAL_CASES = REPO_ROOT / "tests" / "evals" / "cases"
MOCK_DATA = REPO_ROOT / "mcp" / "mock_data"

DATASET_SCHEMA = SCHEMA_DIR / "dataset.schema.json"
ANALYSIS_SCHEMA = SCHEMA_DIR / "analysis.schema.json"
RECOMMENDATIONS_SCHEMA = SCHEMA_DIR / "recommendations.schema.json"


class ValidationFailure(RuntimeError):
    pass


def require_jsonschema():
    try:
        import jsonschema  # noqa: F401
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ValidationFailure(
            "jsonschema is not installed. Install the development extra:\n"
            "    python -m pip install -e '.[dev]'"
        ) from exc
    return Draft202012Validator


def load(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_document(
    document: Any, schema_path: Path, label: str
) -> List[str]:
    """Return a list of human-readable errors; empty means valid."""
    Draft202012Validator = require_jsonschema()
    schema = load(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    messages: List[str] = []
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path) or "(root)"
        messages.append(f"{label}: {location}: {error.message}")
    return messages


def run_cli(arguments: Sequence[str]) -> None:
    process = subprocess.run(
        [sys.executable, "-m", "intralogistics_flow_analyzer", *arguments],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if process.returncode not in (0, 2):
        raise ValidationFailure(
            f"command failed ({process.returncode}): {' '.join(arguments)}\n{process.stderr}"
        )


def build_artifacts(work_dir: Path) -> Dict[str, Path]:
    """Produce the documents the engine is contracted to emit."""
    dataset_path = work_dir / "dataset.json"
    analysis_dir = work_dir / "analysis"
    recommendations_dir = work_dir / "recommendations"

    run_cli(
        [
            "normalize",
            "--input",
            str(EXAMPLE_DIR / "raw"),
            "--mapping",
            str(EXAMPLE_DIR / "mapping.json"),
            "--output",
            str(dataset_path),
        ]
    )
    run_cli(["analyze", "--dataset", str(dataset_path), "--output", str(analysis_dir)])
    run_cli(
        [
            "recommend",
            "--dataset",
            str(dataset_path),
            "--top",
            "10",
            "--output",
            str(recommendations_dir),
        ]
    )
    return {
        "normalized_dataset": dataset_path,
        "analysis": analysis_dir / "analysis.json",
        "recommendations": recommendations_dir / "recommendations.json",
    }


def collect_targets(artifacts: Dict[str, Path]) -> List[Tuple[Any, Path, str]]:
    targets: List[Tuple[Any, Path, str]] = [
        (load(EXAMPLE_DIR / "dataset.json"), DATASET_SCHEMA, "committed example dataset"),
        (
            load(artifacts["normalized_dataset"]),
            DATASET_SCHEMA,
            "dataset produced by ifa normalize",
        ),
        (load(artifacts["analysis"]), ANALYSIS_SCHEMA, "analysis.json"),
        (load(artifacts["recommendations"]), RECOMMENDATIONS_SCHEMA, "recommendations.json"),
    ]
    examples_root = REPO_ROOT / "examples"
    if examples_root.is_dir():
        for directory in sorted(p for p in examples_root.iterdir() if p.is_dir()):
            candidate = directory / "dataset.json"
            if not candidate.is_file():
                continue
            if directory.name == EXAMPLE_DIR.name:
                continue
            targets.append(
                (load(candidate), DATASET_SCHEMA, f"example dataset: {directory.name}")
            )
    for directory in sorted(p for p in MOCK_DATA.iterdir() if p.is_dir()):
        candidate = directory / "dataset.json"
        if candidate.is_file():
            targets.append((load(candidate), DATASET_SCHEMA, f"mcp mock data: {directory.name}"))
    for case_path in sorted(EVAL_CASES.glob("*.json")):
        case = load(case_path)
        if not (isinstance(case, dict) and isinstance(case.get("dataset"), dict)):
            continue
        # A case that exists to be rejected is checked by collect_rejections
        # instead: validating it here would be asserting the opposite of its
        # purpose.
        if case_path.name in _expected_invalid_cases():
            continue
        targets.append(
            (case["dataset"], DATASET_SCHEMA, f"eval case dataset: {case_path.name}")
        )
    return targets


def _expected_invalid_cases() -> Dict[str, str]:
    """Eval cases whose dataset is deliberately outside the schema.

    The key is the file name, the value the schema path fragment that must be
    named in the rejection. Keeping this list explicit means a case cannot become
    silently valid without someone noticing.
    """
    return {"18-provenance-invalid.json": "sequence_basis"}


def collect_rejections() -> List[Tuple[Any, Path, str, str]]:
    """Documents the schemas must refuse, with the field each refusal must name."""
    rejections: List[Tuple[Any, Path, str, str]] = []
    for name, expected_field in _expected_invalid_cases().items():
        case_path = EVAL_CASES / name
        if not case_path.is_file():
            continue
        case = load(case_path)
        dataset = case.get("dataset")
        if isinstance(dataset, dict):
            rejections.append(
                (dataset, DATASET_SCHEMA, f"eval case (must be refused): {name}", expected_field)
            )
    return rejections


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--keep",
        type=Path,
        default=None,
        help="write the generated artefacts here instead of a temporary directory",
    )
    args = parser.parse_args(argv)

    try:
        require_jsonschema()
    except ValidationFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for schema_path in (DATASET_SCHEMA, ANALYSIS_SCHEMA, RECOMMENDATIONS_SCHEMA):
        if not schema_path.is_file():
            print(f"error: missing schema {schema_path}", file=sys.stderr)
            return 1

    if args.keep:
        args.keep.mkdir(parents=True, exist_ok=True)
        work_dir = args.keep
        cleanup = None
    else:
        cleanup = tempfile.TemporaryDirectory()
        work_dir = Path(cleanup.name)

    try:
        artifacts = build_artifacts(work_dir)
        targets = collect_targets(artifacts)
        failures: List[str] = []
        for document, schema_path, label in targets:
            problems = validate_document(document, schema_path, label)
            if problems:
                failures.extend(problems)
                print(f"  FAIL  {label}  ({schema_path.name})")
                for problem in problems[:8]:
                    print(f"          {problem}")
            else:
                print(f"  ok    {label}  ({schema_path.name})")

        # Negative checks: a validator that never refuses anything proves nothing.
        rejections = collect_rejections()
        for document, schema_path, label, expected_field in rejections:
            problems = validate_document(document, schema_path, label)
            if not problems:
                failures.append(f"{label}: the schema accepted a document it must refuse")
                print(f"  FAIL  {label}  (accepted, expected a refusal)")
            elif not any(expected_field in problem for problem in problems):
                failures.append(f"{label}: refused, but not for {expected_field}")
                print(f"  FAIL  {label}  (refused for the wrong reason)")
            else:
                print(f"  ok    {label}  (correctly refused: {expected_field})")
        targets = list(targets) + [(d, s, l) for d, s, l, _ in rejections]
    except ValidationFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if cleanup is not None:
            cleanup.cleanup()

    print()
    if failures:
        print(f"Schema validation FAILED: {len(failures)} error(s)", file=sys.stderr)
        return 1
    print(f"Schema validation passed: {len(targets)} document(s), Draft 2020-12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
