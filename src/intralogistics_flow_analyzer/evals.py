"""Regression evaluation harness.

An eval case is a JSON document that pins the behaviour the engine must keep:

``dataset``                 inline canonical dataset, or ``dataset_path``
``expected_status``         the validation status the case must produce
``required_issue_codes``    codes that must appear
``forbidden_issue_codes``   codes that must not appear
``expected_metrics``        numeric expectations with an explicit ``tolerance``
``allowed_recommendations`` swaps that may appear, as ``"SKU-A|SKU-B"`` pairs
``forbidden_recommendations`` swaps that must never appear
``forbidden_claim_types``   claim types the case must not produce
``expected_claim_types``    claim types the case must produce

Cases are grouped into categories so that ``ifa run-evals`` can report per-area
results: validation, routing, metrics, classification, constraint handling and
unsupported claims.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .graph import build_graph
from .loaders import read_json
from .models import Dataset
from .reporting import analyse
from .validation import validate

CATEGORIES = (
    "validation",
    "routing",
    "metrics",
    "classification",
    "constraint handling",
    "unsupported claims",
)


@dataclass
class CaseResult:
    case_id: str
    category: str
    passed: bool
    checks_passed: int
    checks_total: int
    failures: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "checks_passed": self.checks_passed,
            "checks_total": self.checks_total,
            "failures": list(self.failures),
        }


@dataclass
class EvalReport:
    results: List[CaseResult] = field(default_factory=list)

    def by_category(self) -> Dict[str, Tuple[int, int]]:
        summary: Dict[str, Tuple[int, int]] = {}
        for category in CATEGORIES:
            cases = [r for r in self.results if r.category == category]
            if not cases:
                continue
            summary[category] = (len([c for c in cases if c.passed]), len(cases))
        other = [r for r in self.results if r.category not in CATEGORIES]
        if other:
            summary["other"] = (len([c for c in other if c.passed]), len(other))
        return summary

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results) and bool(self.results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cases": [r.to_dict() for r in self.results],
            "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in self.by_category().items()},
            "overall_passed": len([r for r in self.results if r.passed]),
            "overall_total": len(self.results),
            "passed": self.passed,
        }


def _resolve_dataset(case: Dict[str, Any], case_path: Path) -> Dataset:
    if "dataset" in case:
        return Dataset.from_dict(case["dataset"])
    dataset_path = case.get("dataset_path")
    if not dataset_path:
        raise ValueError(f"{case_path}: case declares neither 'dataset' nor 'dataset_path'.")
    resolved = (case_path.parent / dataset_path).resolve()
    return Dataset.from_dict(read_json(resolved))


def _pair(value: str) -> Tuple[str, str]:
    left, _, right = value.partition("|")
    return tuple(sorted((left.strip(), right.strip())))  # type: ignore[return-value]


def run_case(case_path: Path) -> CaseResult:
    case = read_json(case_path)
    case_id = str(case.get("case_id") or case_path.stem)
    category = str(case.get("category") or "other")
    failures: List[str] = []
    checks = 0

    dataset = _resolve_dataset(case, case_path)
    graph = build_graph(dataset.nodes, dataset.edges)
    report = validate(dataset, graph)

    expected_status = case.get("expected_status")
    if expected_status is not None:
        checks += 1
        if report.status != expected_status:
            failures.append(f"status: expected {expected_status!r}, got {report.status!r}")

    codes = {issue.code for issue in report.issues}
    for code in case.get("required_issue_codes") or []:
        checks += 1
        if code not in codes:
            failures.append(f"missing required issue code {code}")
    for code in case.get("forbidden_issue_codes") or []:
        checks += 1
        if code in codes:
            failures.append(f"forbidden issue code present: {code}")

    expected_exit = case.get("expected_exit_code")
    if expected_exit is not None:
        checks += 1
        if report.exit_code() != int(expected_exit):
            failures.append(
                f"exit code: expected {expected_exit}, got {report.exit_code()}"
            )

    needs_analysis = any(
        key in case
        for key in (
            "expected_metrics",
            "allowed_recommendations",
            "forbidden_recommendations",
            "expected_claim_types",
            "forbidden_claim_types",
            "expected_recommendation_count",
            "expected_rejected_reasons",
            "forbidden_report_substrings",
            "expected_xyz_marker",
            "expected_abc_classes",
            "expected_affinity",
        )
    )
    if needs_analysis:
        result = analyse(dataset, graph=graph)
        analysis = result.analysis

        for name, expectation in (case.get("expected_metrics") or {}).items():
            checks += 1
            actual = analysis["metrics"].get(name)
            if isinstance(expectation, dict):
                target = expectation.get("value")
                tolerance = float(expectation.get("tolerance", 0.0))
            else:
                target, tolerance = expectation, 0.0
            if actual is None or target is None:
                if actual != target:
                    failures.append(f"metric {name}: expected {target!r}, got {actual!r}")
                continue
            if abs(float(actual) - float(target)) > tolerance + 1e-9:
                failures.append(
                    f"metric {name}: expected {target} +/- {tolerance}, got {actual}"
                )

        produced = {
            _pair(f"{r['sku_a']}|{r['sku_b']}") for r in analysis["recommendations"]
        }
        allowed = case.get("allowed_recommendations")
        if allowed is not None:
            checks += 1
            allowed_pairs = {_pair(value) for value in allowed}
            unexpected = produced - allowed_pairs
            if unexpected:
                failures.append(
                    "recommendations outside the allowed set: "
                    + ", ".join("|".join(p) for p in sorted(unexpected))
                )
        for value in case.get("forbidden_recommendations") or []:
            checks += 1
            if _pair(value) in produced:
                failures.append(f"forbidden recommendation produced: {value}")

        expected_count = case.get("expected_recommendation_count")
        if expected_count is not None:
            checks += 1
            if len(analysis["recommendations"]) != int(expected_count):
                failures.append(
                    f"recommendation count: expected {expected_count}, got "
                    f"{len(analysis['recommendations'])}"
                )

        for pair_value, reason in (case.get("expected_rejected_reasons") or {}).items():
            checks += 1
            wanted = _pair(pair_value)
            match = next(
                (
                    r
                    for r in analysis["rejected_candidates"]
                    if _pair(f"{r['sku_a']}|{r['sku_b']}") == wanted
                ),
                None,
            )
            if match is None:
                failures.append(f"expected {pair_value} among rejected candidates")
            elif reason not in (match.get("rejected_because") or []) and reason != match.get(
                "status"
            ):
                failures.append(
                    f"{pair_value} rejected for {match.get('rejected_because')} "
                    f"/ {match.get('status')}, expected {reason}"
                )

        if "expected_xyz_marker" in case:
            checks += 1
            if analysis["xyz_marker"] != case["expected_xyz_marker"]:
                failures.append(
                    f"xyz_marker: expected {case['expected_xyz_marker']!r}, got "
                    f"{analysis['xyz_marker']!r}"
                )

        abc_by_sku = {row["sku"]: row["abc_class"] for row in analysis["abc"]}
        for sku, expected_class in (case.get("expected_abc_classes") or {}).items():
            checks += 1
            if abc_by_sku.get(sku) != expected_class:
                failures.append(
                    f"ABC class of {sku}: expected {expected_class}, got {abc_by_sku.get(sku)}"
                )

        expected_affinity = case.get("expected_affinity")
        if expected_affinity:
            checks += 1
            wanted = _pair(str(expected_affinity.get("pair")))
            match = next(
                (
                    row
                    for row in analysis["affinities"]
                    if _pair(f"{row['sku_a']}|{row['sku_b']}") == wanted
                ),
                None,
            )
            if match is None:
                failures.append(f"affinity pair {expected_affinity.get('pair')} not reported")
            else:
                target = expected_affinity.get("co_pick_count")
                if target is not None and match["co_pick_count"] != target:
                    failures.append(
                        f"co_pick_count for {expected_affinity.get('pair')}: expected {target}, "
                        f"got {match['co_pick_count']}"
                    )

        claim_types = {finding["claim_type"] for finding in analysis["findings"]}
        for claim in case.get("expected_claim_types") or []:
            checks += 1
            if claim not in claim_types:
                failures.append(f"expected a finding with claim_type {claim}")
        for claim in case.get("forbidden_claim_types") or []:
            checks += 1
            if claim in claim_types:
                failures.append(f"forbidden claim_type present: {claim}")

        for substring in case.get("forbidden_report_substrings") or []:
            checks += 1
            blob = json.dumps(analysis, ensure_ascii=False).lower()
            if substring.lower() in blob:
                failures.append(f"forbidden wording present in analysis: {substring!r}")

    return CaseResult(
        case_id=case_id,
        category=category,
        passed=not failures,
        checks_passed=checks - len(failures),
        checks_total=checks,
        failures=failures,
    )


def discover_cases(cases_dir: Path) -> List[Path]:
    cases_dir = Path(cases_dir)
    if not cases_dir.is_dir():
        return []
    return sorted(p for p in cases_dir.glob("*.json"))


def run_evals(cases_dir: Path) -> EvalReport:
    report = EvalReport()
    for path in discover_cases(cases_dir):
        try:
            report.results.append(run_case(path))
        except Exception as exc:  # noqa: BLE001 - a broken case must fail loudly, not crash
            report.results.append(
                CaseResult(
                    case_id=path.stem,
                    category="other",
                    passed=False,
                    checks_passed=0,
                    checks_total=1,
                    failures=[f"case could not be executed: {exc}"],
                )
            )
    return report


def format_report(report: EvalReport) -> str:
    lines: List[str] = []
    summary = report.by_category()
    width = max([len(name) for name in list(summary) + ["overall"]] or [7])
    for category in list(CATEGORIES) + [c for c in summary if c not in CATEGORIES]:
        if category not in summary:
            continue
        passed, total = summary[category]
        state = "PASS" if passed == total else "FAIL"
        lines.append(f"{category.ljust(width)}  {passed}/{total}  {state}")
    total_passed = len([r for r in report.results if r.passed])
    total_cases = len(report.results)
    lines.append(
        f"{'overall'.ljust(width)}  {total_passed}/{total_cases}  "
        f"{'PASS' if report.passed else 'FAIL'}"
    )
    for result in report.results:
        if result.passed:
            continue
        lines.append("")
        lines.append(f"FAILED {result.case_id} ({result.category})")
        for failure in result.failures:
            lines.append(f"  - {failure}")
    return "\n".join(lines)
