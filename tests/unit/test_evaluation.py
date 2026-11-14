"""Unit tests for search evaluation harness (Step 12).

Tests:
- Hand-computed metric verification (P@5, P@10, MRR, nDCG@10)
- Deliberately worsened ranking detected by regression detector
- Dev vs held-out split enforcement
- Reproducibility of reports and offline execution
- Corpus and judgment dataset integrity
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from search_engine.db import discover_migrations, migrate
from search_engine.evaluation.corpus import EVAL_CORPUS
from search_engine.evaluation.evaluator import (
    EVAL_DIR,
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
    check_regression,
    load_baseline,
    save_baseline,
)

# ---------------------------------------------------------------------------
# 1. Hand-computed metric tests
# ---------------------------------------------------------------------------


def test_hand_computed_precision_at_k() -> None:
    # Ground truth: doc 1 (grade 3), doc 2 (1), doc 3 (2), doc 4 (0), doc 5 (2)
    # With min_relevant=2, relevant docs are {1, 3, 5}
    ground_truth = {1: 3, 2: 1, 3: 2, 4: 0, 5: 2}

    # Case A: Top 5 has [1, 2, 3, 4, 5] -> relevant in top 5 are {1, 3, 5} (3/5 = 0.60)
    retrieved = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert calculate_precision_at_k(retrieved, ground_truth, k=5, min_relevant=2) == pytest.approx(
        0.60
    )
    # In top 10, still only 3 relevant docs -> 3/10 = 0.30
    assert calculate_precision_at_k(retrieved, ground_truth, k=10, min_relevant=2) == pytest.approx(
        0.30
    )

    # Case B: Top 5 has all relevant [1, 3, 5, 6, 7] -> 3/5 = 0.60
    assert calculate_precision_at_k([1, 3, 5, 6, 7], ground_truth, k=5) == pytest.approx(0.60)

    # Case C: Empty results
    assert calculate_precision_at_k([], ground_truth, k=5) == 0.0

    # Case D: None relevant
    assert calculate_precision_at_k([2, 4, 6, 7, 8], ground_truth, k=5) == 0.0


def test_hand_computed_mrr() -> None:
    ground_truth = {1: 3, 2: 1, 3: 2}  # Relevant with grade >= 2: {1, 3}

    # First relevant at rank 1 -> 1/1 = 1.0
    assert calculate_mrr([1, 2, 3], ground_truth) == 1.0

    # First relevant at rank 2 -> 1/2 = 0.5
    assert calculate_mrr([2, 3, 1], ground_truth) == 0.5

    # First relevant at rank 3 -> 1/3 = 0.3333...
    assert calculate_mrr([2, 4, 3, 1], ground_truth) == pytest.approx(1.0 / 3.0)

    # No relevant document retrieved
    assert calculate_mrr([2, 4, 5], ground_truth) == 0.0
    assert calculate_mrr([], ground_truth) == 0.0


def test_hand_computed_dcg_and_ndcg() -> None:
    # Grades: rank 1 = 3, rank 2 = 2, rank 3 = 0
    # Gain(3) = 2^3 - 1 = 7. Discount(1) = log2(2) = 1.0 -> 7.0 / 1.0 = 7.0
    # Gain(2) = 2^2 - 1 = 3. Discount(2) = log2(3) = 1.5849625... -> 3.0 / 1.5849625 = 1.892789...
    # Gain(0) = 2^0 - 1 = 0. Discount(3) = log2(4) = 2.0 -> 0.0 / 2.0 = 0.0
    # DCG = 7.0 + (3.0 / math.log2(3)) = 8.892789...
    expected_dcg = 7.0 + (3.0 / math.log2(3))
    assert calculate_dcg([3, 2, 0], k=3) == pytest.approx(expected_dcg)

    # Ideal ranking is [3, 2, 0], so nDCG for [3, 2, 0] must be 1.0
    ground_truth = {101: 3, 102: 2, 103: 0}
    assert calculate_ndcg([101, 102, 103], ground_truth, k=3) == pytest.approx(1.0)

    # Suboptimal ranking: [103, 102, 101] -> grades [0, 2, 3]
    # Rank 1: Gain(0)/log2(2) = 0
    # Rank 2: Gain(2)/log2(3) = 3 / log2(3)
    # Rank 3: Gain(3)/log2(4) = 7 / 2 = 3.5
    sub_dcg = (3.0 / math.log2(3)) + 3.5
    expected_ndcg = sub_dcg / expected_dcg
    assert calculate_ndcg([103, 102, 101], ground_truth, k=3) == pytest.approx(expected_ndcg)

    # Edge cases
    assert calculate_ndcg([], ground_truth, k=5) == 0.0
    assert calculate_ndcg([999], {999: 0}, k=5) == 1.0  # idcg is 0 and dcg is 0 -> 1.0


# ---------------------------------------------------------------------------
# 2. Regression detector tests
# ---------------------------------------------------------------------------


def test_regression_detection_detects_deliberately_worsened_ranking(tmp_path: Path) -> None:
    baseline_data = {
        "mean_p_at_5": 0.50,
        "mean_p_at_10": 0.40,
        "mean_mrr": 0.85,
        "mean_ndcg_10": 0.80,
    }
    baseline_file = tmp_path / "baseline.json"
    save_baseline(baseline_data, baseline_file)

    # Case A: Identical report -> passes
    res_identical = check_regression(baseline_data, baseline_file, tolerance=0.05)
    assert res_identical.passed is True
    assert len(res_identical.regressions) == 0

    # Case B: Slight change within tolerance -> passes
    slight_drop = {
        "mean_p_at_5": 0.48,  # drop of 0.02 <= 0.05
        "mean_p_at_10": 0.39,  # drop of 0.01 <= 0.05
        "mean_mrr": 0.83,  # drop of 0.02 <= 0.05
        "mean_ndcg_10": 0.78,  # drop of 0.02 <= 0.05
    }
    res_slight = check_regression(slight_drop, baseline_file, tolerance=0.05)
    assert res_slight.passed is True

    # Case C: Deliberately worsened ranking (> 0.05 drop) -> fails
    worsened = {
        "mean_p_at_5": 0.30,  # drop of 0.20 > 0.05
        "mean_p_at_10": 0.20,  # drop of 0.20 > 0.05
        "mean_mrr": 0.50,  # drop of 0.35 > 0.05
        "mean_ndcg_10": 0.60,  # drop of 0.20 > 0.05
    }
    res_worsened = check_regression(worsened, baseline_file, tolerance=0.05)
    assert res_worsened.passed is False
    assert len(res_worsened.regressions) == 4
    for r in res_worsened.regressions:
        assert "dropped from" in r

    # Case D: Significant improvement -> passes and logs improvement
    improved = {
        "mean_p_at_5": 0.65,  # +0.15
        "mean_p_at_10": 0.55,  # +0.15
        "mean_mrr": 0.95,  # +0.10
        "mean_ndcg_10": 0.92,  # +0.12
    }
    res_improved = check_regression(improved, baseline_file, tolerance=0.05)
    assert res_improved.passed is True
    assert len(res_improved.improvements) == 4


# ---------------------------------------------------------------------------
# 3. Dev vs held-out split enforcement
# ---------------------------------------------------------------------------


def test_split_enforcement_and_query_loading() -> None:
    queries = load_queries()
    assert len(queries) >= 20, f"Expected >= 20 queries, got {len(queries)}"

    dev_queries = [q for q in queries if q.split == "dev"]
    held_out_queries = [q for q in queries if q.split == "held_out"]

    assert len(dev_queries) >= 12
    assert len(held_out_queries) >= 6
    assert len(dev_queries) + len(held_out_queries) == len(queries)

    # Ensure query IDs are unique
    qids = [q.query_id for q in queries]
    assert len(qids) == len(set(qids))


def test_invalid_split_raises_value_error() -> None:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="Unknown split: 'invalid'"):
        evaluate_system(conn, split="invalid")


# ---------------------------------------------------------------------------
# 4. Dataset and corpus integrity
# ---------------------------------------------------------------------------


def test_corpus_integrity() -> None:
    assert len(EVAL_CORPUS) == 60, f"Expected 60 documents in EVAL_CORPUS, got {len(EVAL_CORPUS)}"

    urls = set()
    ids = set()
    valid_doc_types = {"page", "pdf", "text", "news"}

    for doc in EVAL_CORPUS:
        assert doc.id not in ids, f"Duplicate doc id: {doc.id}"
        ids.add(doc.id)
        assert doc.url not in urls, f"Duplicate doc url: {doc.url}"
        urls.add(doc.url)
        assert doc.title.strip(), f"Empty title in doc {doc.id}"
        assert doc.body.strip(), f"Empty body in doc {doc.id}"
        assert doc.doc_type in valid_doc_types, f"Invalid doc_type {doc.doc_type} in doc {doc.id}"
        assert len(doc.lang) >= 2, f"Invalid lang {doc.lang} in doc {doc.id}"

    # IDs must be contiguous 1..60
    assert sorted(ids) == list(range(1, 61))


def test_judgments_integrity() -> None:
    judgments = load_judgments()
    queries = load_queries()
    valid_qids = {q.query_id for q in queries}

    for qid, doc_grades in judgments.items():
        assert qid in valid_qids, f"Judgment references unknown query_id {qid}"
        for doc_id, grade in doc_grades.items():
            assert 1 <= doc_id <= 60, f"Judgment references invalid doc_id {doc_id}"
            assert 0 <= grade <= 3, f"Judgment grade {grade} out of [0, 3] bounds"


# ---------------------------------------------------------------------------
# 5. End-to-end evaluation and reproducibility
# ---------------------------------------------------------------------------


def test_evaluation_reproducibility_and_offline() -> None:
    conn1 = sqlite3.connect(":memory:")
    migrate(conn1, discover_migrations())
    populate_eval_database(conn1)

    report1 = evaluate_system(conn1, split="dev")
    assert report1.total_queries >= 30
    assert report1.mean_mrr > 0.8
    assert report1.mean_ndcg_10 > 0.7
    assert report1.latency_p50_ms < 200.0  # local SQLite under pytest coverage tracing

    # Re-run on separate database
    conn2 = sqlite3.connect(":memory:")
    migrate(conn2, discover_migrations())
    populate_eval_database(conn2)

    report2 = evaluate_system(conn2, split="dev")

    # Metrics must match exactly across runs
    assert report1.mean_p_at_5 == pytest.approx(report2.mean_p_at_5)
    assert report1.mean_p_at_10 == pytest.approx(report2.mean_p_at_10)
    assert report1.mean_mrr == pytest.approx(report2.mean_mrr)
    assert report1.mean_ndcg_10 == pytest.approx(report2.mean_ndcg_10)

    # Markdown rendering check
    md = report1.render_markdown()
    assert "# Search Evaluation Report [DEV SET]" in md
    assert "Precision@5" in md
    assert "nDCG@10" in md

    # Baseline file loading check
    baseline_path = EVAL_DIR / "baseline.json"
    assert baseline_path.exists()
    baseline = load_baseline(baseline_path)
    assert baseline["split"] == "dev"
    assert "mean_ndcg_10" in baseline
