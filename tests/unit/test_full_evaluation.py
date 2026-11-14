"""Unit tests for Step 27: Full Evaluation, Daily-Use Checklist & Benchmarks.

Validates all Section 7 numeric thresholds, fixture search quality, panel precision,
grounding (Invariant 10), synthetic corpus generation, and daily-use checklist automation.
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

from search_engine.cli import main
from search_engine.config import Settings
from search_engine.db import discover_migrations, migrate
from search_engine.evaluation.daily_use import run_daily_use_checklist
from search_engine.evaluation.evaluator import (
    EVAL_DIR,
    evaluate_system,
    load_queries,
    populate_eval_database,
)
from search_engine.evaluation.synthetic import (
    generate_synthetic_corpus,
    run_synthetic_benchmarks,
)
from search_engine.experience.clusters import cluster_news_items
from search_engine.indexer import decompress_text
from search_engine.search import SearchService

# ---------------------------------------------------------------------------
# 1. Deterministic Synthetic Corpus Generator Tests
# ---------------------------------------------------------------------------


def test_synthetic_corpus_generator_is_deterministic() -> None:
    docs1, news1 = generate_synthetic_corpus(seed=42, num_docs=100, num_news=50)
    docs2, news2 = generate_synthetic_corpus(seed=42, num_docs=100, num_news=50)

    assert len(docs1) == 100
    assert len(news1) == 50
    assert len(docs2) == 100
    assert len(news2) == 50

    # Ensure identical contents for same seed
    for d1, d2 in zip(docs1, docs2, strict=True):
        assert d1.id == d2.id
        assert d1.url == d2.url
        assert d1.title == d2.title
        assert d1.body == d2.body

    for n1, n2 in zip(news1, news2, strict=True):
        assert n1.id == n2.id
        assert n1.title == n2.title
        assert n1.link == n2.link

    # Different seed produces different content
    docs_diff, _ = generate_synthetic_corpus(seed=999, num_docs=100, num_news=50)
    assert docs1[0].title != docs_diff[0].title


# ---------------------------------------------------------------------------
# 2. Section 7 Performance Benchmarks
# ---------------------------------------------------------------------------


def test_section_7_performance_benchmarks() -> None:
    rep = run_synthetic_benchmarks(seed=42)

    assert rep.all_passed is True, f"Benchmark failed: {rep.render_markdown()}"
    assert rep.num_documents == 1000
    assert rep.num_news_items == 500

    # Section 7 Limits:
    assert rep.index_build_seconds < 120.0, f"Index build too slow: {rep.index_build_seconds}s"
    assert rep.search_latency_p50_ms < 80.0, f"Search p50 too high: {rep.search_latency_p50_ms}ms"
    assert rep.search_latency_p95_ms < 300.0, f"Search p95 too high: {rep.search_latency_p95_ms}ms"
    assert rep.home_latency_p95_ms < 200.0, f"Home p95 too high: {rep.home_latency_p95_ms}ms"
    assert rep.serving_memory_rss_mb < 300.0, f"Serving RSS too high: {rep.serving_memory_rss_mb}MB"
    assert rep.crawl_memory_rss_mb < 600.0, f"Crawl RSS too high: {rep.crawl_memory_rss_mb}MB"

    # Markdown rendering
    md = rep.render_markdown()
    assert "# Performance & Benchmark Report [PASS]" in md
    assert "Index Build Time" in md
    assert "Search Latency p50" in md


# ---------------------------------------------------------------------------
# 3. Daily-Use 10-Point Checklist Automation
# ---------------------------------------------------------------------------


def test_daily_use_checklist_automation() -> None:
    report = run_daily_use_checklist()

    assert report.total_steps == 10
    assert report.passed_steps == 10
    assert report.passed is True

    # Verify each step explicitly
    step_names = [r.name for r in report.results]
    assert step_names == [
        "Open home",
        "Read story link",
        "Search",
        "Read synopsis",
        "Open sidebar source",
        "Search offline",
        "Recover from empty query",
        "Use filter",
        "Follow suggestion",
        "Restore from backup",
    ]

    for r in report.results:
        assert r.passed is True, f"Step {r.step_num} ({r.name}) failed: {r.message}"

    md = report.render_markdown()
    assert "# Daily-Use 10-Point Checklist Report [PASS]" in md
    assert "10/10 steps" in md


def test_daily_use_and_benchmark_cli(capsys: pytest.CaptureFixture[str]) -> None:
    # Test CLI daily-use
    ret_daily = main(["daily-use"])
    assert ret_daily == 0
    captured = capsys.readouterr()
    assert "Daily-Use 10-Point Checklist Report [PASS]" in captured.out

    # Test CLI daily-use --json
    ret_daily_json = main(["daily-use", "--json"])
    assert ret_daily_json == 0
    captured_json = capsys.readouterr()
    assert '"passed": true' in captured_json.out

    # Test CLI benchmark --json
    ret_bench = main(["benchmark", "--json"])
    assert ret_bench == 0
    captured_bench = capsys.readouterr()
    assert '"all_passed": true' in captured_bench.out


# ---------------------------------------------------------------------------
# 4. Strict Section 7 Fixture Search Quality on Held-Out Queries
# ---------------------------------------------------------------------------


def test_section_7_fixture_search_quality_on_held_out() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn, discover_migrations())
    populate_eval_database(conn)

    report = evaluate_system(conn, split="held_out")

    # Gate: at least 50 queries overall, at least 15 held-out
    all_queries = load_queries()
    assert len(all_queries) >= 50, f"Expected >= 50 queries, got {len(all_queries)}"
    assert report.total_queries >= 15, (
        f"Expected >= 15 held-out queries, got {report.total_queries}"
    )

    # Section 7 numeric thresholds:
    assert report.mean_p_at_5 >= 0.60, (
        f"Precision@5 failed threshold: {report.mean_p_at_5:.4f} < 0.60"
    )
    assert report.mean_mrr >= 0.70, f"MRR failed threshold: {report.mean_mrr:.4f} < 0.70"
    assert report.mean_ndcg_10 >= 0.70, (
        f"nDCG@10 failed threshold: {report.mean_ndcg_10:.4f} < 0.70"
    )


# ---------------------------------------------------------------------------
# 5. Panel Quality & Invariant 10 Grounding
# ---------------------------------------------------------------------------


def test_panel_cases_grounding_and_negatives() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn, discover_migrations())
    populate_eval_database(conn)

    settings = Settings()
    search_service = SearchService(conn, settings)

    cases_path = EVAL_DIR / "panel_cases.csv"
    assert cases_path.is_file(), "eval/panel_cases.csv missing"

    with cases_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) >= 15, f"Expected >= 15 panel cases, got {len(rows)}"

    for row in rows:
        _ = row["case_id"]
        cat = row["category"]
        q = row["query"]

        resp = search_service.search(q)

        if cat == "negative":
            # Section 7: zero panels on the negative query set
            assert resp.panel is None, (
                f"Expected None panel for negative query '{q}', got {resp.panel}"
            )
            assert resp.synopsis is None, f"Expected None synopsis for negative query '{q}'"

        elif cat == "negative_stopword":
            # Stopword queries yield null synopsis
            assert resp.synopsis is None, f"Expected None synopsis for stopword query '{q}'"

        elif cat == "synopsis_positive":
            # Section 7: 100 percent of synopsis text found verbatim in cited stored source
            assert resp.synopsis is not None, f"Expected synopsis for '{q}'"
            syn = resp.synopsis
            row_doc = conn.execute(
                "SELECT text_gz FROM documents WHERE final_url = ?", (syn.url,)
            ).fetchone()
            assert row_doc is not None, f"Cited source {syn.url} not in database"
            doc_text = decompress_text(row_doc["text_gz"])
            assert syn.text in doc_text, f"Synopsis not verbatim in doc text: {syn.text}"

        elif cat == "preview_positive":
            assert resp.panel is not None, f"Expected preview panel for '{q}'"
            assert resp.panel.kind == "preview"
            assert resp.panel.title
            assert resp.panel.url
            assert resp.panel.domain


# ---------------------------------------------------------------------------
# 6. News Clustering Quality on Labeled Set
# ---------------------------------------------------------------------------


def test_news_clustering_quality_on_labels() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn, discover_migrations())

    labels_path = EVAL_DIR / "news_labels.csv"
    assert labels_path.is_file(), "eval/news_labels.csv missing"

    gold_clusters: dict[str, str] = {}
    sources: set[int] = set()

    with labels_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item_id = row["item_id"]
            gold_clusters[item_id] = row["cluster_label"]
            sources.add(int(row["source_id"]))

    with conn:
        for sid in sources:
            conn.execute(
                """
                INSERT OR IGNORE INTO news_sources
                    (id, feed_url_key, name, category, poll_interval_minutes, enabled)
                VALUES (?, ?, ?, ?, 30, 1)
                """,
                (sid, f"https://source{sid}.com/rss", f"Source {sid}", "General"),
            )

        with labels_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                conn.execute(
                    """
                    INSERT INTO news_items
                        (id, source_id, title, link, published_at, summary,
                         category, status, first_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, '', 'General', 'active', ?, ?)
                    """,
                    (
                        row["item_id"],
                        int(row["source_id"]),
                        row["title"],
                        f"https://source{row['source_id']}.com/{row['item_id']}",
                        row["published_at"],
                        row["published_at"],
                        row["published_at"],
                    ),
                )

    clusters = cluster_news_items(conn, min_jaccard=0.60)
    assert len(clusters) > 0

    # Compute pairwise clustering precision:
    # If two items share a cluster in predicted, do they share in gold?
    correct_pairs = 0
    total_predicted_pairs = 0

    for cl in clusters:
        member_rows = conn.execute(
            "SELECT id FROM news_items WHERE cluster_id = ?", (cl.id,)
        ).fetchall()
        member_ids = [r["id"] for r in member_rows]
        for i in range(len(member_ids)):
            for j in range(i + 1, len(member_ids)):
                total_predicted_pairs += 1
                if gold_clusters.get(member_ids[i]) == gold_clusters.get(member_ids[j]):
                    correct_pairs += 1

    precision = (correct_pairs / total_predicted_pairs) if total_predicted_pairs > 0 else 1.0
    # Section 7 Gate: cluster precision at least 0.90
    assert precision >= 0.90, f"News cluster precision failed gate: {precision:.4f} < 0.90"
