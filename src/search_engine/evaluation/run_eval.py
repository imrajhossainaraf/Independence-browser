"""Command-line runner for the evaluation harness (Step 12)."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from search_engine.db import discover_migrations, migrate
from search_engine.evaluation.evaluator import (
    EVAL_DIR,
    evaluate_system,
    populate_eval_database,
)
from search_engine.evaluation.regression import (
    DEFAULT_TOLERANCE,
    check_regression,
    save_baseline,
)


def build_eval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search-engine-eval",
        description="Run search quality and latency evaluation harness.",
    )
    parser.add_argument(
        "--split",
        choices=["dev", "held_out", "all"],
        default="dev",
        help="Query split to evaluate (default: dev).",
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=EVAL_DIR / "queries.csv",
        help="Path to queries CSV file.",
    )
    parser.add_argument(
        "--judgments",
        type=Path,
        default=EVAL_DIR / "judgments.csv",
        help="Path to judgments CSV file.",
    )
    parser.add_argument(
        "--save-baseline",
        type=Path,
        default=None,
        help="Path to save the evaluation output as baseline JSON.",
    )
    parser.add_argument(
        "--check-baseline",
        type=Path,
        default=None,
        help="Path to baseline JSON file for regression detection.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help=f"Allowed metric regression threshold (default: {DEFAULT_TOLERANCE}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON report instead of Markdown.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional file path to write the output report.",
    )
    return parser


def run_evaluation(args: argparse.Namespace) -> int:
    """Run evaluation in an in-memory database and report results."""
    conn = sqlite3.connect(":memory:")
    migrate(conn, discover_migrations())
    populate_eval_database(conn)

    report = evaluate_system(
        conn,
        split=args.split,
        queries_file=args.queries,
        judgments_file=args.judgments,
    )

    if args.json:
        output_text = json.dumps(report.to_dict(), indent=2)
    else:
        output_text = report.render_markdown()

    print(output_text)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
        print(f"\n[Report saved to {args.output}]")

    if args.save_baseline:
        save_baseline(report, args.save_baseline)
        print(f"\n[Baseline saved to {args.save_baseline}]")

    # Regression check
    baseline_path = args.check_baseline
    if baseline_path and baseline_path.exists():
        result = check_regression(report, baseline_path, tolerance=args.tolerance)
        print("\n=== Regression Check ===")
        print(result.summary())
        for metric, info in result.details.items():
            diff = info["diff"]
            print(
                f"  {metric:15s}: baseline={info['baseline']:.4f}, "
                f"current={info['current']:.4f}, diff={diff:+.4f}"
            )
        if not result.passed:
            return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_eval_parser()
    args = parser.parse_args(argv)
    return run_evaluation(args)


if __name__ == "__main__":
    sys.exit(main())
