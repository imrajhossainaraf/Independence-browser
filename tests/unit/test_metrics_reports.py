"""Unit tests for Step 26 metrics and diagnostic reports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from search_engine.cli import main
from search_engine.db import Database
from search_engine.reporting.metrics import (
    compute_percentile,
    gather_all_metrics,
    gather_crawl_metrics,
    gather_entity_metrics,
    gather_image_metrics,
    gather_index_metrics,
    gather_news_metrics,
    gather_serving_metrics,
    record_metric,
    record_metrics_batch,
)
from search_engine.storage import FileStore


def test_compute_percentile_known_values() -> None:
    """Percentile calculation matches known mathematical properties."""
    # 1. Empty list
    assert compute_percentile([], 50) == 0.0

    # 2. Single value
    assert compute_percentile([42.0], 0) == 42.0
    assert compute_percentile([42.0], 50) == 42.0
    assert compute_percentile([42.0], 99) == 42.0
    assert compute_percentile([42.0], 100) == 42.0

    # 3. Two values: [10.0, 20.0]
    assert compute_percentile([10.0, 20.0], 0) == 10.0
    assert compute_percentile([10.0, 20.0], 50) == 15.0
    assert compute_percentile([10.0, 20.0], 100) == 20.0

    # 4. Standard distribution [1..100]
    vals_100 = list(range(1, 101))
    # p50 of 1..100 is 50.5
    assert compute_percentile(vals_100, 50) == 50.5
    # p95 of 1..100 is 95.05
    assert compute_percentile(vals_100, 95) == 95.05
    # p99 of 1..100 is 99.01
    assert compute_percentile(vals_100, 99) == 99.01

    # 5. Out of bounds bounds clamping
    assert compute_percentile([10, 20, 30], -10) == 10.0
    assert compute_percentile([10, 20, 30], 150) == 30.0


def test_metrics_persistence_across_restarts(tmp_path: Path) -> None:
    """Metrics recorded in SQLite survive database restart and reopen."""
    db_path = tmp_path / "metrics_test.db"
    db = Database(db_path).open()
    try:
        record_metric(db, "test_counter", 1.0, labels="run1")
        record_metric(db, "test_counter", 2.0, labels="run2")
        record_metrics_batch(
            db,
            [
                ("search_latency_ms", 12.5, "q1"),
                ("search_latency_ms", 45.0, "q2"),
                ("search_latency_ms", 8.2, "q3"),
            ],
        )
    finally:
        db.close()

    # Reopen database - verify persistence
    db2 = Database(db_path).open()
    try:
        with db2.reader() as conn:
            rows = conn.execute("SELECT name, value, labels FROM metrics ORDER BY id").fetchall()
            assert len(rows) == 5
            assert str(rows[0][0]) == "test_counter"
            assert float(rows[0][1]) == 1.0
            assert str(rows[0][2]) == "run1"
            assert str(rows[1][0]) == "test_counter"
            assert float(rows[1][1]) == 2.0

            serving_rep = gather_serving_metrics(db2)
            assert serving_rep.search_count == 3
            # sorted latencies: [8.2, 12.5, 45.0] -> p50 is 12.5
            assert serving_rep.search_latency_p50_ms == 12.5
            assert serving_rep.search_latency_p95_ms > 12.5
    finally:
        db2.close()


def test_zero_data_reports(tmp_path: Path) -> None:
    """Diagnostic reports work gracefully with completely empty database."""
    db = Database(tmp_path / "empty.db").open()
    try:
        crawl = gather_crawl_metrics(db)
        assert crawl.urls_discovered == 0
        assert crawl.requests_completed == 0
        assert crawl.documents_stored == 0
        assert crawl.crawl_throughput_pages_per_sec == 0.0

        news = gather_news_metrics(db)
        assert news.total_items == 0
        assert news.clusters_count == 0
        assert news.items_per_source == {}

        images = gather_image_metrics(db)
        assert images.stored_count == 0
        assert images.bytes_used == 0

        entities = gather_entity_metrics(db)
        assert entities.count == 0
        assert entities.oldest_retrieval_at is None

        idx = gather_index_metrics(db)
        assert idx.indexed_documents == 0
        assert idx.fts_row_count == 0

        serving = gather_serving_metrics(db)
        assert serving.search_count == 0
        assert serving.search_latency_p50_ms == 0.0
        assert serving.avg_results_per_query == 0.0
        assert serving.empty_result_rate == 0.0
        assert serving.peak_memory_mb > 0.0

        full = gather_all_metrics(db)
        rendered = full.render()
        assert "DIAGNOSTIC REPORT" in rendered
        assert "System Memory:" in rendered
        dict_data = full.to_dict()
        assert "crawl" in dict_data
        assert "news" in dict_data
    finally:
        db.close()


def test_fixture_populated_reports(tmp_path: Path) -> None:
    """Reports accurately represent populated fixture data and failure states."""
    db = Database(tmp_path / "populated.db").open()
    store = FileStore(tmp_path / "store")
    raw_hash = store.put_raw(b"<html>Mock Page Content</html>")
    img_data = b"RIFF\x20\x00\x00\x00WEBPVP8 \x10\x00\x00\x00"
    img_hash = hashlib.sha256(img_data).hexdigest()
    store.put_image(img_hash, 160, img_data)

    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    now_iso = now.isoformat()
    t_1h_ago = (now - timedelta(hours=1)).isoformat()
    t_2d_ago = (now - timedelta(days=2)).isoformat()

    try:
        with db.transaction() as conn:
            # 1. Hosts and URLs
            conn.execute(
                """
                INSERT INTO hosts (id, host, registered_domain, created_at)
                VALUES (1, 'site1.com', 'site1.com', ?), (2, 'site2.org', 'site2.org', ?)
                """,
                (now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO urls (
                    id, url_key, host_id, status, last_reason, source, discovered_at, updated_at
                )
                VALUES
                (1, 'https://site1.com/p1', 1, 'done', 'fetched', 'seed', ?, ?),
                (2, 'https://site1.com/p2', 1, 'blocked', 'robots_disallow', 'link', ?, ?),
                (3, 'https://site2.org/a', 2, 'done', '304', 'seed', ?, ?)
                """,
                (now_iso, now_iso, now_iso, now_iso, now_iso, now_iso),
            )

            # 2. Documents
            conn.execute(
                """
                INSERT INTO documents (
                    id, url_id, doc_type, final_url, title, text_gz, raw_hash, status,
                    indexed_version, fetched_at, updated_at
                )
                VALUES (
                    1, 1, 'page', 'https://site1.com/p1', 'Page One', NULL, ?, 'active', 1, ?, ?
                )
                """,
                (raw_hash, now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO documents (
                    id, url_id, doc_type, final_url, title, text_gz, raw_hash, status,
                    canonical_group_id, indexed_version, fetched_at, updated_at
                )
                VALUES (
                    2, 3, 'page', 'https://site2.org/a', 'Page Two Dup',
                    NULL, ?, 'active', 1, 1, ?, ?
                )
                """,
                (raw_hash, now_iso, now_iso),
            )

            # 3. News sources & items
            conn.execute(
                """
                INSERT INTO news_sources (
                    id, feed_url_key, name, category, poll_interval_minutes,
                    consecutive_failures, last_error, enabled
                )
                VALUES
                (1, 'https://news.org/feed', 'News Org', 'Tech', 30, 0, NULL, 1),
                (2, 'https://broken.com/rss', 'Broken Feed', 'General', 60, 3, 'http_500', 1)
                """
            )
            conn.execute(
                """
                INSERT INTO news_items (
                    id, source_id, link, title, summary, published_at, category,
                    status, first_seen_at, updated_at
                )
                VALUES
                ('n1', 1, 'https://news.org/story1', 'Great News', 'Story summary',
                 ?, 'Tech', 'active', ?, ?),
                ('n2', 1, 'https://news.org/story2', 'Older News', 'Story summary 2',
                 ?, 'Tech', 'active', ?, ?)
                """,
                (t_1h_ago, t_1h_ago, t_1h_ago, t_2d_ago, t_2d_ago, t_2d_ago),
            )
            conn.execute(
                """
                INSERT INTO news_clusters (id, headline_item_id, created_at, updated_at)
                VALUES (1, 'n1', ?, ?)
                """,
                (now_iso, now_iso),
            )

            # 4. Images
            conn.execute(
                """
                INSERT INTO images (
                    id, source_url, content_hash, width, height, bytes, status, fetched_at
                )
                VALUES ('img-1', 'https://site1.com/img.jpg', ?, 160, 160, 1024, 'active', ?)
                """,
                (img_hash, now_iso),
            )

            # 5. Entities
            conn.execute(
                """
                INSERT INTO entities (
                    id, name, type, summary, source_url, license, attribution, retrieved_at
                )
                VALUES (
                    1, 'Albert Einstein', 'Person', 'Physicist',
                    'https://wiki.org/Einstein', 'CC BY-SA', 'Wiki', ?
                )
                """,
                (t_2d_ago,),
            )

            # 6. Events (representing failures)
            conn.execute(
                """
                INSERT INTO events (run_id, at, reason, subject, detail)
                VALUES
                ('crawl-1', ?, 'failed', 'https://site1.com/err500', 'http_500'),
                ('crawl-1', ?, 'failed', 'https://site1.com/err404', 'http_404'),
                ('crawl-1', ?, 'image_rejected', 'https://site1.com/img.svg', 'svg_forbidden')
                """,
                (now_iso, now_iso, now_iso),
            )

            # 7. Metrics (crawl & serving)
            conn.execute(
                """
                INSERT INTO metrics (at, name, value, labels)
                VALUES
                (?, 'crawl_duration_seconds', 4.0, 'crawl-1'),
                (?, 'crawl_bytes_downloaded', 2048, 'crawl-1'),
                (?, 'search_latency_ms', 10.0, 'q1'),
                (?, 'search_latency_ms', 20.0, 'q2'),
                (?, 'search_latency_ms', 30.0, 'q3'),
                (?, 'search_results_count', 5.0, 'q1'),
                (?, 'search_results_count', 0.0, 'q2'),
                (?, 'search_panel_shown', 1.0, 'q1'),
                (?, 'search_panel_shown', 0.0, 'q2')
                """,
                (
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )

        # Test Crawl Metrics
        crawl = gather_crawl_metrics(db)
        assert crawl.urls_discovered == 3
        assert crawl.requests_completed == 2
        assert crawl.robots_blocks == 1
        assert crawl.documents_stored == 2
        assert crawl.documents_indexed == 2
        assert crawl.duplicates_detected == 1
        assert crawl.http_errors_by_class == {"http_404": 1, "http_500": 1}
        assert crawl.crawl_duration_seconds == 4.0
        assert crawl.crawl_throughput_pages_per_sec == 0.5
        assert crawl.bytes_downloaded == 2048
        crawl_text = crawl.render()
        assert "URLs Discovered:          3" in crawl_text
        assert "http_500: 1" in crawl_text

        # Test News Metrics
        news = gather_news_metrics(db, now=now)
        assert news.total_items == 2
        assert news.clusters_count == 1
        assert news.items_per_source["News Org"] == 2
        assert news.feed_failures == {"Broken Feed": 3}
        assert "News Org" in news.newest_item_age_seconds
        assert news.newest_item_age_seconds["News Org"] == 3600.0  # 1 hour ago
        news_text = news.render()
        assert "Broken Feed: 3 consecutive failures" in news_text

        # Test Image Metrics
        imgs = gather_image_metrics(db)
        assert imgs.stored_count == 1
        assert imgs.rejected_by_reason == {"svg_forbidden": 1}
        img_text = imgs.render()
        assert "Stored Thumbnails:        1" in img_text
        assert "svg_forbidden: 1" in img_text

        # Test Entity Metrics
        ents = gather_entity_metrics(db)
        assert ents.count == 1
        assert ents.oldest_retrieval_at == t_2d_ago
        ent_text = ents.render()
        assert "Total Entities:           1" in ent_text

        # Test Serving Metrics
        serving = gather_serving_metrics(db)
        assert serving.search_count == 3
        assert serving.search_latency_p50_ms == 20.0
        assert serving.search_latency_p95_ms > 20.0
        assert serving.avg_results_per_query == 2.5
        assert serving.empty_result_rate == 0.5  # 1 out of 2 queries returned 0
        assert serving.panel_shown_rate == 0.5  # 1 out of 2 queries had panel
        assert serving.panel_null_rate == 0.5
        serving_text = serving.render()
        assert "Empty Result Rate:        50.0%" in serving_text
        assert "Search Latency p50:       20.00 ms" in serving_text

    finally:
        db.close()


def test_cli_report_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI search-engine report outputs both text and JSON for all kinds."""
    db = Database(tmp_path / "cli_rep.db").open()
    db_path = str(db.path)
    try:
        record_metric(db, "search_latency_ms", 15.0)
    finally:
        db.close()

    # 1. Report crawl
    rc = main(["report", "crawl", "--db", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CRAWL METRICS" in out

    # 2. Report crawl JSON
    rc = main(["report", "crawl", "--db", db_path, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "urls_discovered" in data

    # 3. Report news JSON
    rc = main(["report", "news", "--db", db_path, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "total_items" in data

    # 4. Report index JSON
    rc = main(["report", "index", "--db", db_path, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "index_version" in data

    # 5. Report search JSON
    rc = main(["report", "search", "--db", db_path, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["search_count"] == 1
    assert data["search_latency_p50_ms"] == 15.0

    # 6. Report all (default)
    rc = main(["report", "--db", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "INDEPENDENT SEARCH ENGINE DIAGNOSTIC REPORT" in out
    assert "CRAWL METRICS" in out
    assert "NEWS METRICS" in out
    assert "IMAGE METRICS" in out
    assert "ENTITY METRICS" in out
    assert "INDEX METRICS" in out
    assert "SERVING METRICS" in out

    # 7. Report all JSON
    rc = main(["report", "all", "--db", db_path, "--json"])
    assert rc == 0
    full_data = json.loads(capsys.readouterr().out)
    assert "generated_at" in full_data
    assert "serving" in full_data
    assert full_data["serving"]["search_count"] == 1
