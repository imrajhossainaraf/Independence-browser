"""Regression detection against saved baseline reports for search evaluation (Step 12)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from search_engine.evaluation.evaluator import EvaluationReport

DEFAULT_TOLERANCE: float = 0.05
EVAL_METRICS: tuple[str, ...] = (
    "mean_p_at_5",
    "mean_p_at_10",
    "mean_mrr",
    "mean_ndcg_10",
)


@dataclass(frozen=True, slots=True)
class RegressionResult:
    passed: bool
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    details: dict[str, dict[str, float]] = field(default_factory=dict)

    def summary(self) -> str:
        if self.passed:
            status = "PASSED (no regressions detected)"
            if self.improvements:
                status += f" with {len(self.improvements)} improvements"
            return status
        return (
            f"FAILED ({len(self.regressions)} regressions detected: {'; '.join(self.regressions)})"
        )


def save_baseline(report: EvaluationReport | dict[str, Any], path: Path) -> None:
    """Save an evaluation report to JSON baseline file."""
    data = report.to_dict() if isinstance(report, EvaluationReport) else report
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_baseline(path: Path) -> dict[str, Any]:
    """Load baseline data from JSON file."""
    if not path.exists():
        raise FileNotFoundError(f"Baseline file not found at: {path}")
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
        return data


def check_regression(
    current: EvaluationReport | dict[str, Any],
    baseline: Path | dict[str, Any],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    metrics: tuple[str, ...] = EVAL_METRICS,
) -> RegressionResult:
    """Compare current evaluation report against baseline.

    Fails if any monitored metric drops by more than the allowed tolerance.
    """
    curr_data = current.to_dict() if isinstance(current, EvaluationReport) else current
    base_data = load_baseline(baseline) if isinstance(baseline, Path) else baseline

    regressions: list[str] = []
    improvements: list[str] = []
    details: dict[str, dict[str, float]] = {}

    for metric in metrics:
        if metric not in base_data:
            continue
        base_val = float(base_data[metric])
        curr_val = float(curr_data.get(metric, 0.0))
        diff = curr_val - base_val

        details[metric] = {
            "baseline": base_val,
            "current": curr_val,
            "diff": diff,
        }

        # Check for regression (drop exceeds tolerance)
        if diff < -tolerance:
            regressions.append(
                f"{metric}: dropped from {base_val:.4f} to {curr_val:.4f} "
                f"(diff {diff:+.4f} exceeds -{tolerance:.4f} tolerance)"
            )
        elif diff > tolerance:
            improvements.append(
                f"{metric}: improved from {base_val:.4f} to {curr_val:.4f} (diff {diff:+.4f})"
            )

    passed = len(regressions) == 0
    return RegressionResult(
        passed=passed,
        regressions=regressions,
        improvements=improvements,
        details=details,
    )
