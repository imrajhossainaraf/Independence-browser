"""Evaluation harness for search relevance, precision, and latency (Step 12)."""

from __future__ import annotations

from search_engine.evaluation.corpus import EVAL_CORPUS, EvalDocument
from search_engine.evaluation.evaluator import (
    EVAL_DIR,
    EvaluationReport,
    QueryMetrics,
    QueryRecord,
    calculate_dcg,
    calculate_mrr,
    calculate_ndcg,
    calculate_precision_at_k,
    evaluate_system,
    load_judgments,
    load_queries,
    populate_eval_database,
)
from search_engine.evaluation.regression import (
    DEFAULT_TOLERANCE,
    EVAL_METRICS,
    RegressionResult,
    check_regression,
    load_baseline,
    save_baseline,
)

__all__ = [
    "DEFAULT_TOLERANCE",
    "EVAL_CORPUS",
    "EVAL_DIR",
    "EVAL_METRICS",
    "EvalDocument",
    "EvaluationReport",
    "QueryMetrics",
    "QueryRecord",
    "RegressionResult",
    "calculate_dcg",
    "calculate_mrr",
    "calculate_ndcg",
    "calculate_precision_at_k",
    "check_regression",
    "evaluate_system",
    "load_baseline",
    "load_judgments",
    "load_queries",
    "populate_eval_database",
    "save_baseline",
]
