"""Command line interface: ``ifa`` / ``python -m intralogistics_flow_analyzer``.

Sub-commands
------------
``normalize``  CSV exports + explicit mapping -> canonical dataset.json
``validate``   deterministic checks; exit code 0 / 1 / 2
``analyze``    routes, metrics, classification, affinities, recommendations, reports
``recommend``  constraint-checked, simulated pair swaps only
``simulate``   replay history against an explicit move list
``run-evals``  regression cases under tests/evals/cases
``describe``   dataset summary without running an analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from .evals import format_report, run_evals
from .graph import build_graph
from .loaders import LoaderError, load_dataset, read_json, write_json, write_text
from .models import ENGINE_VERSION, SCHEMA_VERSION
from .normalizer import NormalizationError, load_mapping, normalize
from .reporting import analyse, write_recommendations_csv, write_reports
from .routing import build_routes
from .simulation import SimulationError, parse_moves, simulate_moves
from .slotting import recommend as recommend_swaps
from .validation import EXIT_BLOCKING, EXIT_OK, EXIT_PARTIAL, validate

PROGRAM = "ifa"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Intralogistics Flow & Slotting Analyzer — Community Edition. "
            "Deterministic warehouse flow analysis, spaghetti diagrams and "
            "constraint-safe slotting simulations, computed locally."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {ENGINE_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    normalize_parser = sub.add_parser(
        "normalize", help="Turn CSV exports into a canonical dataset.json using an explicit mapping."
    )
    normalize_parser.add_argument("--input", required=True, type=Path, help="Directory of CSV exports.")
    normalize_parser.add_argument("--mapping", required=True, type=Path, help="mapping.json path.")
    normalize_parser.add_argument("--output", required=True, type=Path, help="dataset.json to write.")

    validate_parser = sub.add_parser("validate", help="Validate a canonical dataset.")
    validate_parser.add_argument("--dataset", required=True, type=Path)
    validate_parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    validate_parser.add_argument(
        "--output", type=Path, default=None, help="Optional path for the JSON validation report."
    )

    analyze_parser = sub.add_parser("analyze", help="Run the full analysis and write every report.")
    analyze_parser.add_argument("--dataset", required=True, type=Path)
    analyze_parser.add_argument("--output", required=True, type=Path)
    analyze_parser.add_argument("--top", type=int, default=None, help="Maximum recommendations.")
    analyze_parser.add_argument(
        "--spaghetti-order", type=str, default=None, help="Draw a single order instead of the top N."
    )
    analyze_parser.add_argument(
        "--spaghetti-top", type=int, default=None, help="Number of routes in spaghetti.svg."
    )

    recommend_parser = sub.add_parser(
        "recommend", help="Produce constraint-checked, simulated pair swaps."
    )
    recommend_parser.add_argument("--dataset", required=True, type=Path)
    recommend_parser.add_argument("--output", required=True, type=Path)
    recommend_parser.add_argument("--top", type=int, default=10)

    simulate_parser = sub.add_parser("simulate", help="Replay history against an explicit move list.")
    simulate_parser.add_argument("--dataset", required=True, type=Path)
    simulate_parser.add_argument("--moves", required=True, type=Path)
    simulate_parser.add_argument("--output", required=True, type=Path)

    evals_parser = sub.add_parser("run-evals", help="Run the deterministic regression cases.")
    evals_parser.add_argument(
        "--cases-dir",
        type=Path,
        default=Path("tests/evals/cases"),
        help="Directory containing eval case files (default: tests/evals/cases).",
    )
    evals_parser.add_argument("--json", action="store_true", help="Emit the eval report as JSON.")

    describe_parser = sub.add_parser("describe", help="Summarise a dataset without analysing it.")
    describe_parser.add_argument("--dataset", required=True, type=Path)

    return parser


def _print_issues(issues: Sequence[dict]) -> None:
    for issue in issues:
        affected = ", ".join(issue["affected_records"][:6])
        suffix = f" [{affected}]" if affected else ""
        print(f"  {issue['severity'].upper():7} {issue['code']}: {issue['message']}{suffix}")


def command_normalize(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping)
    result = normalize(args.input, mapping)
    write_json(args.output, result.dataset)
    print(f"Wrote canonical dataset: {args.output}")
    for issue in result.issues:
        print(f"  {issue.severity.upper():7} {issue.code}: {issue.message}")
    if result.dropped_columns:
        print(
            "  Dropped worker-identifying columns (never used, never reported): "
            + ", ".join(sorted(result.dropped_columns))
        )
    return EXIT_OK


def command_validate(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    report = validate(dataset)
    document = report.to_dict()
    if args.output:
        write_json(args.output, document)
    if args.json:
        print(json.dumps(document, indent=2, ensure_ascii=False))
    else:
        print(f"Dataset: {args.dataset}")
        print(f"Status: {report.status}")
        print(
            f"Orders: {report.routable_orders} routable / {report.total_orders} total "
            f"({report.unroutable_orders} excluded)"
        )
        print(f"Errors: {len(report.errors)}  Warnings: {len(report.warnings)}")
        if report.issues:
            print("Issues:")
            _print_issues(document["issues"])
        else:
            print("No issue raised.")
    return report.exit_code()


def command_analyze(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    result = analyse(dataset, top_recommendations=args.top)
    paths = write_reports(
        result,
        args.output,
        spaghetti_order=args.spaghetti_order,
        spaghetti_top=args.spaghetti_top,
    )
    analysis = result.analysis
    print(f"Status: {analysis['status']}")
    print(
        f"Routed orders: {analysis['data_quality']['orders_routed']}"
        f"/{analysis['data_quality']['orders_total']}  "
        f"Total distance: {analysis['metrics']['total_distance_m']} m"
    )
    print(f"Recommendations: {len(analysis['recommendations'])}")
    for key in sorted(paths):
        print(f"  {key}: {paths[key]}")
    return result.validation.exit_code()


def command_recommend(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    report = validate(dataset)
    if report.status == "data_error":
        print("Dataset status is data_error; no recommendation is produced.")
        _print_issues([i.to_dict() for i in report.errors])
        return EXIT_BLOCKING
    graph = build_graph(dataset.nodes, dataset.edges)
    routing = build_routes(dataset, graph)
    slotting = recommend_swaps(dataset, graph, routing, args.top)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    document = slotting.to_dict()
    document.update(
        {
            "schema_version": SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "dataset_id": dataset.meta.dataset_id,
            "status": report.status,
        }
    )
    write_json(output / "recommendations.json", document)
    write_recommendations_csv(output / "recommendations.csv", slotting.recommendations)
    write_json(output / "moves.json", slotting.moves_document())

    print(f"Candidates generated: {slotting.generated_candidates}")
    print(f"Candidates simulated: {slotting.evaluated_candidates}")
    print(f"Recommendations: {len(slotting.recommendations)}")
    for recommendation in slotting.recommendations:
        print(
            f"  {recommendation['recommendation_id']}: {recommendation['sku_a']} "
            f"{recommendation['from_a']}->{recommendation['to_a']} / "
            f"{recommendation['sku_b']} {recommendation['from_b']}->{recommendation['to_b']}  "
            f"estimated pick-distance reduction {recommendation['estimated_reduction_m']} m "
            f"({recommendation['estimated_reduction_percent']}%)"
        )
    print(f"  recommendations.json: {output / 'recommendations.json'}")
    print(f"  recommendations.csv: {output / 'recommendations.csv'}")
    print(f"  moves.json: {output / 'moves.json'}")
    return report.exit_code()


def command_simulate(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    report = validate(dataset)
    if report.status == "data_error":
        print("Dataset status is data_error; no simulation is performed.")
        _print_issues([i.to_dict() for i in report.errors])
        return EXIT_BLOCKING
    graph = build_graph(dataset.nodes, dataset.edges)
    moves = parse_moves(read_json(args.moves))
    comparison = simulate_moves(dataset, graph, moves)

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    document = comparison.to_dict()
    document.update(
        {
            "schema_version": SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "dataset_id": dataset.meta.dataset_id,
            "status": report.status,
        }
    )
    write_json(output / "simulation.json", document)

    lines = [
        "# Simulation result",
        "",
        f"Dataset `{dataset.meta.dataset_id}` · engine `{ENGINE_VERSION}`",
        "",
        f"- Constraint status: **{comparison.constraint_status}**",
        f"- Baseline distance: {comparison.baseline_distance_m:.2f} m",
        f"- Simulated distance: {comparison.simulated_distance_m:.2f} m",
        f"- Estimated pick-distance reduction: {comparison.estimated_reduction_m:.2f} m",
        f"- Affected historical orders: {comparison.affected_orders}",
        "",
        "## Assumptions",
        "",
    ]
    lines += [f"- {a}" for a in document["assumptions"]]
    lines += ["", "## Limitations", ""]
    lines += [f"- {l}" for l in document["limitations"]]
    lines += ["", "A simulated reduction is not a realised saving.", ""]
    write_text(output / "simulation.md", "\n".join(lines))

    print(f"Constraint status: {comparison.constraint_status}")
    if comparison.constraint_status != "ok":
        for name, reason in comparison.constraint_reasons.items():
            print(f"  {name}: {reason}")
    print(f"Baseline distance: {comparison.baseline_distance_m:.2f} m")
    print(f"Simulated distance: {comparison.simulated_distance_m:.2f} m")
    print(
        f"Estimated pick-distance reduction: {comparison.estimated_reduction_m:.2f} m "
        f"({comparison.estimated_reduction_percent:.2f}%)"
        if comparison.estimated_reduction_percent is not None
        else f"Estimated pick-distance reduction: {comparison.estimated_reduction_m:.2f} m"
    )
    print(f"Affected orders: {comparison.affected_orders}")
    print(f"  simulation.json: {output / 'simulation.json'}")
    print(f"  simulation.md: {output / 'simulation.md'}")
    return report.exit_code()


def command_run_evals(args: argparse.Namespace) -> int:
    report = run_evals(args.cases_dir)
    if not report.results:
        print(f"No eval case found in {args.cases_dir}.")
        return EXIT_BLOCKING
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
    return EXIT_OK if report.passed else EXIT_BLOCKING


def command_describe(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    graph = build_graph(dataset.nodes, dataset.edges)
    orders = dataset.orders()
    print(f"Dataset: {dataset.meta.dataset_id}")
    print(f"  fictional: {dataset.meta.fictional}")
    print(f"  period: {dataset.meta.period_start} .. {dataset.meta.period_end}")
    print(f"  nodes: {len(dataset.nodes)}  edges: {len(dataset.edges)}")
    print(f"  travel modes present: {', '.join(graph.modes()) or 'none'}")
    print(
        f"  locations: {len(dataset.locations)} "
        f"(pick: {len([l for l in dataset.locations if l.location_type == 'pick'])})"
    )
    print(f"  items: {len(dataset.items)}  assignments: {len(dataset.assignments)}")
    print(f"  orders: {len(orders)}  pick lines: {len(dataset.picks)}")
    return EXIT_OK


HANDLERS = {
    "normalize": command_normalize,
    "validate": command_validate,
    "analyze": command_analyze,
    "recommend": command_recommend,
    "simulate": command_simulate,
    "run-evals": command_run_evals,
    "describe": command_describe,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = HANDLERS[args.command]
    try:
        return handler(args)
    except (LoaderError, NormalizationError, SimulationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BLOCKING
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BLOCKING


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
