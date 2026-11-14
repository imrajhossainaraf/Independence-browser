"""Unit and integration tests for news ingestion and headline clustering (Step 18, decision D16)."""

from __future__ import annotations

import csv
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.fixtures.server import FixtureHandler, running_fixture_server

from search_engine.config import load_settings
from search_engine.crawler.news import (
    NewsPoller,
)
from search_engine.db import Database
from search_engine.experience.clusters import (
    cluster_news_items,
    compute_top_story_score,
    jaccard_similarity,
    title_shingles,
)
from search_engine.experience.news import (
    apply_news_retention,
    clean_summary,
    get_sources_health,
    parse_news_sources,
)
from search_engine.indexer.build import upsert_document_index
from search_engine.indexer.consistency import verify_index_consistency
from search_engine.lock import ProcessLock
from search_engine.search import SearchService

pytestmark = pytest.mark.loopback


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "news_test.db").open()
    yield db
    db.close()


def test_parse_news_sources_config() -> None:
    lines = [
        "# Comment line",
        "",
        "https://example.com/rss.xml, Tech Feed, Technology, 45, yes",
        "https://news.org/feed, Daily News, General, 20, no",
        "https://minimal.com/feed, Minimal Feed",
        "https://clamped.com/feed, Fast Feed, News, 5, 1",
    ]
    sources = parse_news_sources(lines)
    assert len(sources) == 4

    assert sources[0].feed_url == "https://example.com/rss.xml"
    assert sources[0].name == "Tech Feed"
    assert sources[0].category == "Technology"
    assert sources[0].poll_interval_minutes == 45
    assert sources[0].images_allowed is True

    assert sources[1].images_allowed is False
    assert sources[1].poll_interval_minutes == 20

    # Defaults
    assert sources[2].category == "General"
    assert sources[2].poll_interval_minutes == 30
    assert sources[2].images_allowed is False

    # Clamping: minimum is 15 minutes per D16 and DB constraint
    assert sources[3].poll_interval_minutes == 15
    assert sources[3].images_allowed is True


def test_clean_summary_strips_markup_and_scripts() -> None:
    raw = (
        "<div><script>alert('xss');</script><p>Breaking news: "
        "<b>Space mission</b> succeeds &amp; lands safely.</p>"
        "<style>body { color: red; }</style></div>"
    )
    cleaned = clean_summary(raw, max_chars=300)
    assert "<script>" not in cleaned
    assert "alert" not in cleaned
    assert "<style>" not in cleaned
    assert "color: red" not in cleaned
    assert "<b>" not in cleaned
    assert "Breaking news: Space mission succeeds & lands safely." in cleaned

    # Length capping at 300 characters
    long_raw = "word " * 100
    cleaned_long = clean_summary(long_raw, max_chars=300)
    assert len(cleaned_long) <= 300


def test_title_shingles_and_jaccard_boundary() -> None:
    # 5 words: bigrams are 4: (a,b), (b,c), (c,d), (d,e)
    t1 = "alpha beta gamma delta epsilon"
    # Shares 3 bigrams: (a,b), (b,c), (c,d); union is 5 -> 3/5 = 0.60
    t2 = "alpha beta gamma delta zeta"
    # Shares 2 bigrams; union is 6 -> 2/6 = 0.333 < 0.60
    t3 = "alpha beta gamma eta theta"

    s1 = title_shingles(t1)
    s2 = title_shingles(t2)
    s3 = title_shingles(t3)

    sim_1_2 = jaccard_similarity(s1, s2)
    sim_1_3 = jaccard_similarity(s1, s3)

    assert sim_1_2 == pytest.approx(0.60, abs=1e-4)
    assert sim_1_2 >= 0.60  # Boundary check: at least 0.6 clusters!
    assert sim_1_3 < 0.60  # Below boundary: does NOT cluster!


def test_48_hour_window_clustering(test_db: Database) -> None:
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    now_iso = now.isoformat()

    with test_db.transaction() as conn:
        conn.execute(
            "INSERT INTO news_sources (id, feed_url_key, name, category, poll_interval_minutes) "
            "VALUES (1, 'http://s1/feed', 'Source 1', 'News', 30), "
            "(2, 'http://s2/feed', 'Source 2', 'News', 30)"
        )
        # Pair within 47 hours: should cluster
        sql_insert = (
            "INSERT INTO news_items (id, source_id, title, link, published_at, "
            "summary, category, first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, '', 'News', ?, ?)"
        )
        conn.execute(
            sql_insert,
            (
                "item-a1",
                1,
                "Major Scientific Discovery Announced",
                "http://s1/a1",
                (now - timedelta(hours=47)).isoformat(),
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            sql_insert,
            (
                "item-a2",
                2,
                "Major Scientific Discovery Announced Today",
                "http://s2/a2",
                now.isoformat(),
                now_iso,
                now_iso,
            ),
        )
        # Pair 49 hours apart: should NOT cluster
        conn.execute(
            sql_insert,
            (
                "item-b1",
                1,
                "Old Historic Discovery Announced",
                "http://s1/b1",
                (now - timedelta(hours=50)).isoformat(),
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            sql_insert,
            (
                "item-b2",
                2,
                "Old Historic Discovery Announced Today",
                "http://s2/b2",
                now.isoformat(),
                now_iso,
                now_iso,
            ),
        )

        clusters = cluster_news_items(conn, now=now, window_hours=48.0)

    # Only pair A should form a cluster
    assert len(clusters) == 1
    assert "item-a1" in clusters[0].item_ids
    assert "item-a2" in clusters[0].item_ids
    assert "item-b1" not in clusters[0].item_ids
    assert "item-b2" not in clusters[0].item_ids


def test_story_reported_by_three_sources_forms_one_cluster(test_db: Database) -> None:
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    now_iso = now.isoformat()

    with test_db.transaction() as conn:
        conn.execute(
            "INSERT INTO news_sources "
            "(id, feed_url_key, name, category, poll_interval_minutes, consecutive_failures) "
            "VALUES (1, 'http://s1/feed', 'Source 1', 'News', 30, 0), "
            "(2, 'http://s2/feed', 'Source 2', 'News', 30, 0), "
            "(3, 'http://s3/feed', 'Source 3', 'News', 30, 0)"
        )
        sql_insert = (
            "INSERT INTO news_items (id, source_id, title, link, published_at, "
            "summary, category, first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, '', 'Tech', ?, ?)"
        )
        conn.execute(
            sql_insert,
            (
                "i1",
                1,
                "SpaceX Launches Starship On Third Orbital Test Flight",
                "http://s1/1",
                (now - timedelta(hours=2)).isoformat(),
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            sql_insert,
            (
                "i2",
                2,
                "SpaceX Launches Starship On Third Orbital Test Flight Successfully",
                "http://s2/2",
                (now - timedelta(hours=1)).isoformat(),
                now_iso,
                now_iso,
            ),
        )
        conn.execute(
            sql_insert,
            (
                "i3",
                3,
                "SpaceX Launches Starship On Third Orbital Test Flight From Texas",
                "http://s3/3",
                now.isoformat(),
                now_iso,
                now_iso,
            ),
        )

        clusters = cluster_news_items(conn, now=now)

    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.distinct_sources == 3
    assert set(cluster.item_ids) == {"i1", "i2", "i3"}
    # Earliest item from highest quality source (all equal failures, s1 has lowest id; i1 is 2h ago)
    assert cluster.headline_item_id == "i1"


def test_top_stories_ordering() -> None:
    # Formula: distinct_sources * exp(-age_in_hours / 12)
    s_fresh_3 = compute_top_story_score(3, 0.0)  # 3 * 1.0 = 3.0
    s_fresh_2 = compute_top_story_score(2, 0.0)  # 2 * 1.0 = 2.0
    s_old_3 = compute_top_story_score(3, 12.0)  # 3 * e^-1 ~= 1.1036
    s_very_old_3 = compute_top_story_score(3, 24.0)  # 3 * e^-2 ~= 0.406

    assert s_fresh_3 == pytest.approx(3.0)
    assert s_fresh_2 == pytest.approx(2.0)
    assert s_old_3 == pytest.approx(3.0 * 0.367879, rel=1e-3)
    assert s_fresh_3 > s_fresh_2 > s_old_3 > s_very_old_3


def test_retention_and_cap_pruning(test_db: Database) -> None:
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    now_iso = now.isoformat()

    with test_db.transaction() as conn:
        conn.execute(
            "INSERT INTO news_sources (id, feed_url_key, name, category, poll_interval_minutes) "
            "VALUES (1, 'http://s1/feed', 'Source 1', 'News', 30)"
        )

        sql_items = (
            "INSERT INTO news_items (id, source_id, title, link, published_at, "
            "summary, category, first_seen_at, updated_at, status) "
            "VALUES (?, 1, ?, ?, ?, '', 'News', ?, ?, 'active')"
        )
        sql_docs = (
            "INSERT INTO documents "
            "(news_item_id, doc_type, final_url, title, status, word_count, "
            "published_at, fetched_at, updated_at) "
            "VALUES (?, 'news', ?, ?, 'active', 0, ?, ?, ?)"
        )

        # 1. Insert 2 expired items (> 14 days old)
        for i in range(2):
            item_id = f"exp-{i}"
            pub = (now - timedelta(days=15 + i)).isoformat()
            conn.execute(
                sql_items,
                (item_id, f"Expired {i}", f"http://s1/{item_id}", pub, now_iso, now_iso),
            )
            cur = conn.execute(
                sql_docs,
                (item_id, f"http://s1/{item_id}", f"Expired {i}", pub, now_iso, now_iso),
            )
            assert cur.lastrowid is not None
            upsert_document_index(conn, cur.lastrowid)

        # 2. Insert 8 fresh items (< 14 days old)
        for i in range(8):
            item_id = f"fresh-{i}"
            pub = (now - timedelta(days=i)).isoformat()
            conn.execute(
                sql_items,
                (item_id, f"Fresh {i}", f"http://s1/{item_id}", pub, now_iso, now_iso),
            )
            cur = conn.execute(
                sql_docs,
                (item_id, f"http://s1/{item_id}", f"Fresh {i}", pub, now_iso, now_iso),
            )
            assert cur.lastrowid is not None
            upsert_document_index(conn, cur.lastrowid)

        # Apply retention: max_days=14, max_active=5
        expired, cap_pruned = apply_news_retention(conn, now=now, max_days=14, max_active=5)

        assert expired == 2
        # 8 fresh items capped at 5 -> 3 oldest fresh items pruned
        assert cap_pruned == 3

        # Verify active count in DB
        active_items = conn.execute(
            "SELECT count(*) FROM news_items WHERE status = 'active'"
        ).fetchone()[0]
        assert active_items == 5

        # Verify documents status synced
        active_docs = conn.execute(
            "SELECT count(*) FROM documents WHERE status = 'active'"
        ).fetchone()[0]
        assert active_docs == 5

        # Index consistency verification passes
        report = verify_index_consistency(conn)
        assert report.is_consistent


def test_source_health_and_stale_flag(test_db: Database) -> None:
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)

    with test_db.transaction() as conn:
        conn.execute(
            "INSERT INTO news_sources (id, feed_url_key, name, category, poll_interval_minutes, "
            "last_success_at, consecutive_failures) VALUES "
            "(1, 'http://s1/feed', 'Healthy', 'News', 30, ?, 0), "
            "(2, 'http://s2/feed', 'Failing', 'News', 30, ?, 2), "
            "(3, 'http://s3/feed', 'StaleTime', 'News', 30, ?, 0), "
            "(4, 'http://s4/feed', 'NeverPolled', 'News', 30, NULL, 0)",
            (
                (now - timedelta(minutes=10)).isoformat(),
                (now - timedelta(hours=1)).isoformat(),
                (now - timedelta(minutes=75)).isoformat(),  # > 2 * 30 min
            ),
        )

        health = get_sources_health(conn, now=now)

    assert len(health) == 4
    # Source 1: Healthy and not stale
    assert health[0].is_healthy is True
    assert health[0].is_stale is False

    # Source 2: Failing -> unhealthy and stale
    assert health[1].is_healthy is False
    assert health[1].is_stale is True

    # Source 3: Old success (> 2*interval) -> stale
    assert health[2].is_healthy is True
    assert health[2].is_stale is True

    # Source 4: Never polled -> stale
    assert health[3].is_healthy is False
    assert health[3].is_stale is True


def test_cluster_precision_on_eval_labels(test_db: Database) -> None:
    eval_file = Path("eval/news_labels.csv")
    assert eval_file.exists(), "eval/news_labels.csv must exist"

    rows = list(csv.DictReader(eval_file.open(encoding="utf-8")))
    now = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)

    with test_db.transaction() as conn:
        # Create sources 1..4
        for s_id in range(1, 5):
            conn.execute(
                "INSERT INTO news_sources "
                "(id, feed_url_key, name, category, poll_interval_minutes) "
                "VALUES (?, ?, 'Source', 'News', 30)",
                (s_id, f"http://source{s_id}.org/feed"),
            )

        for r in rows:
            conn.execute(
                "INSERT INTO news_items (id, source_id, title, link, published_at, "
                "summary, category, first_seen_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, '', 'News', ?, ?)",
                (
                    r["item_id"],
                    int(r["source_id"]),
                    r["title"],
                    f"http://test.org/{r['item_id']}",
                    r["published_at"],
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

        clusters = cluster_news_items(conn, now=now)

    # Build ground-truth lookup
    label_map = {r["item_id"]: r["cluster_label"] for r in rows}

    # Verify cluster precision:
    # For every pair in each predicted cluster, they must share the same ground-truth label!
    total_pairs = 0
    correct_pairs = 0

    for cl in clusters:
        ids = cl.item_ids
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                total_pairs += 1
                if label_map[ids[i]] == label_map[ids[j]]:
                    correct_pairs += 1

    precision = (correct_pairs / total_pairs) if total_pairs > 0 else 1.0
    # Must meet or exceed 0.90 threshold specified in BUILD_INSTRUCTIONS.md line 174
    assert precision >= 0.90
    assert len(clusters) == 4  # spacex_starship, fed_rates, jwst_galaxy, climate_pact


def test_news_poller_end_to_end_fixture_server(tmp_path: Path) -> None:
    with running_fixture_server() as server_info:
        feed_url = server_info.url_for("/news/feed.xml")

        rss_content = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<rss version="2.0">\n'
            b"<channel>\n"
            b"  <title>Local Tech Dispatch</title>\n"
            b"  <link>" + feed_url.encode() + b"</link>\n"
            b"  <description>Latest technology news</description>\n"
            b"  <item>\n"
            b"    <title>Quantum Processor Breaks Coherence Record</title>\n"
            b"    <link>" + server_info.url_for("/articles/quantum").encode() + b"</link>\n"
            b"    <pubDate>Fri, 14 Nov 2026 10:00:00 GMT</pubDate>\n"
            b"    <description>&lt;p&gt;Physicists achieved a 10s coherence time.&lt;/p&gt;"
            b"</description>\n"
            b"    <category>Science</category>\n"
            b"  </item>\n"
            b"</channel>\n"
            b"</rss>"
        )

        def handle_robots(h: object) -> None:
            h._send_bytes(b"User-agent: *\nAllow: /\n", "text/plain")  # type: ignore[attr-defined]

        def handle_feed(h: object) -> None:
            h._send_bytes(rss_content, "application/rss+xml")  # type: ignore[attr-defined]

        server_info.server.register_handler("/robots.txt", handle_robots)
        server_info.server.register_handler("/news/feed.xml", handle_feed)

        db_path = tmp_path / "news_poller.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sources_file = data_dir / "news_sources.txt"
        sources_file.write_text(f"{feed_url}, Local Tech, Science, 30, no\n", encoding="utf-8")

        settings = load_settings(
            None,
            {
                "db_path": str(db_path),
                "allow_loopback_for_tests": "true",
            },
        )

        poller = NewsPoller(settings, mode="fixture")
        report = poller.poll(sources_file=sources_file, force=True)

        assert report.status == "success"
        assert report.sources_success == 1
        assert report.items_added == 1

        # Check document in database: doc_type = 'news', no article body
        with Database(db_path) as db, db.reader() as conn:
            doc_row = conn.execute(
                "SELECT doc_type, title, description, text_gz FROM documents "
                "WHERE doc_type = 'news'"
            ).fetchone()
            assert doc_row is not None
            assert doc_row["doc_type"] == "news"
            assert doc_row["title"] == "Quantum Processor Breaks Coherence Record"
            assert "Physicists achieved" in doc_row["description"]
            # Invariant: NEVER store article body for home page news items
            assert doc_row["text_gz"] is None

            # Check search offline
            service = SearchService(conn)
            res = service.search("Quantum Processor")
            assert len(res.results) >= 1
            assert res.results[0].title == "Quantum Processor Breaks Coherence Record"


def test_news_poller_304_not_modified(tmp_path: Path) -> None:
    with running_fixture_server() as server_info:
        feed_url = server_info.url_for("/news/etag_feed.xml")

        rss_content = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<rss version="2.0"><channel><title>Etag Feed</title>'
            b"<item><title>Item One</title><link>" + feed_url.encode() + b"#1</link>"
            b"<description>Test</description></item>"
            b"</channel></rss>"
        )

        def handle_feed(h: FixtureHandler) -> None:
            req_etag = h.headers.get("if-none-match")
            if req_etag == '"etag-xyz"':
                h.send_response(304)
                h.send_header("ETag", '"etag-xyz"')
                h.send_header("Content-Length", "0")
                h.end_headers()
            else:
                h.send_response(200)
                h.send_header("Content-Type", "application/rss+xml")
                h.send_header("ETag", '"etag-xyz"')
                h.send_header("Content-Length", str(len(rss_content)))
                h.end_headers()
                h.wfile.write(rss_content)

        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_bytes(b"User-agent: *\nAllow: /\n", "text/plain")
        )
        server_info.server.register_handler("/news/etag_feed.xml", handle_feed)

        db_path = tmp_path / "etag.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sources_file = data_dir / "news_sources.txt"
        sources_file.write_text(f"{feed_url}, ETag Source, General, 30, no\n", encoding="utf-8")

        settings = load_settings(
            None,
            {
                "db_path": str(db_path),
                "allow_loopback_for_tests": "true",
            },
        )

        poller = NewsPoller(settings, mode="fixture")

        # First poll: 200 OK
        r1 = poller.poll(sources_file=sources_file, force=True)
        assert r1.sources_success == 1
        assert r1.items_added == 1

        # Second poll: 304 Not Modified (reset host delay for immediate poll)
        poller.scheduler.host_next_allowed.clear()
        r2 = poller.poll(sources_file=sources_file, force=True)
        assert r2.sources_success == 1
        assert r2.sources_not_modified == 1
        assert r2.items_added == 0


def test_failing_feed_keeps_old_items(tmp_path: Path) -> None:
    with running_fixture_server() as server_info:
        feed_url = server_info.url_for("/news/failing.xml")

        rss_content = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<rss version="2.0"><channel><title>Failing Feed</title>'
            b"<item><title>Old Stable News Item</title><link>"
            + feed_url.encode()
            + b"#stable</link>"
            b"<description>Still visible</description></item>"
            b"</channel></rss>"
        )

        state = {"failing": False}

        def handle_feed(h: FixtureHandler) -> None:
            if state["failing"]:
                h._send_response_simple(500, b"Server Error", "text/plain")
            else:
                h._send_bytes(rss_content, "application/rss+xml")

        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_bytes(b"User-agent: *\nAllow: /\n", "text/plain")
        )
        server_info.server.register_handler("/news/failing.xml", handle_feed)

        db_path = tmp_path / "failing.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sources_file = data_dir / "news_sources.txt"
        sources_file.write_text(f"{feed_url}, Flaky Source, General, 30, no\n", encoding="utf-8")

        settings = load_settings(
            None,
            {
                "db_path": str(db_path),
                "allow_loopback_for_tests": "true",
            },
        )

        poller = NewsPoller(settings, mode="fixture")

        # 1. First poll succeeds
        r1 = poller.poll(sources_file=sources_file, force=True)
        assert r1.sources_success == 1
        assert r1.items_added == 1

        # 2. Server fails with 500 on second poll
        state["failing"] = True
        r2 = poller.poll(sources_file=sources_file, force=True)
        assert r2.sources_failed == 1

        # 3. Source is unhealthy, but old item remains active and indexed
        with Database(db_path) as db, db.reader() as conn:
            health = get_sources_health(conn)
            assert health[0].consecutive_failures == 1
            assert health[0].is_healthy is False

            service = SearchService(conn)
            res = service.search("Old Stable News")
            assert len(res.results) == 1
            assert res.results[0].title == "Old Stable News Item"


def test_missing_dates_flagged(tmp_path: Path) -> None:
    with running_fixture_server() as server_info:
        feed_url = server_info.url_for("/news/nodate.xml")

        rss_content = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<rss version="2.0"><channel><title>No Date Feed</title>'
            b"<item><title>Breaking Undated Story</title><link>"
            + feed_url.encode()
            + b"#undated</link>"
            b"<description>No pubdate element provided</description></item>"
            b"</channel></rss>"
        )

        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_bytes(b"User-agent: *\nAllow: /\n", "text/plain")
        )
        server_info.server.register_handler(
            "/news/nodate.xml", lambda h: h._send_bytes(rss_content, "application/rss+xml")
        )

        db_path = tmp_path / "nodate.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sources_file = data_dir / "news_sources.txt"
        sources_file.write_text(f"{feed_url}, Undated Source, General, 30, no\n", encoding="utf-8")

        settings = load_settings(
            None,
            {
                "db_path": str(db_path),
                "allow_loopback_for_tests": "true",
            },
        )

        poller = NewsPoller(settings, mode="fixture")
        report = poller.poll(sources_file=sources_file, force=True)
        assert report.items_added == 1

        with Database(db_path) as db, db.reader() as conn:
            item = conn.execute(
                "SELECT published_is_first_seen, published_at FROM news_items "
                "WHERE title = 'Breaking Undated Story'"
            ).fetchone()
            assert item is not None
            # Flagged as first-seen time fallback
            assert item["published_is_first_seen"] == 1
            assert item["published_at"] is not None


def test_overlapping_polls_refused_by_lock(tmp_path: Path) -> None:
    lock_file = tmp_path / "data" / "news_poll.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    settings = load_settings(
        None,
        {
            "db_path": str(tmp_path / "locked.db"),
            "allow_loopback_for_tests": "true",
        },
    )

    # Acquire lock in advance
    with ProcessLock(lock_file):
        poller = NewsPoller(settings, mode="fixture", lock_path=lock_file)
        report = poller.poll()
        assert report.status == "refused_lock_held"
        assert len(report.errors) == 1
        assert "held by another active process" in report.errors[0]


def test_stale_lock_recovery(tmp_path: Path) -> None:
    lock_file = tmp_path / "data" / "news_poll.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    # Write stale metadata with non-existent dead PID
    lock_file.write_text(
        '{"pid": 9999999, "acquired_at": "2026-01-01T00:00:00Z"}\n', encoding="utf-8"
    )

    settings = load_settings(
        None,
        {
            "db_path": str(tmp_path / "stale.db"),
            "allow_loopback_for_tests": "true",
        },
    )

    poller = NewsPoller(settings, mode="fixture", lock_path=lock_file)
    report = poller.poll()
    # Stale lock should recover cleanly and succeed
    assert report.status == "success"


def test_robots_disallowed_feed_reported_not_fetched(tmp_path: Path) -> None:
    with running_fixture_server() as server_info:
        feed_url = server_info.url_for("/news/secret_feed.xml")

        def handle_robots(h: object) -> None:
            h._send_bytes(b"User-agent: *\nDisallow: /news/secret_feed.xml\n", "text/plain")  # type: ignore[attr-defined]

        called = {"fetch": False}

        def handle_feed(h: object) -> None:
            called["fetch"] = True
            h._send_status(200)  # type: ignore[attr-defined]

        server_info.server.register_handler("/robots.txt", handle_robots)
        server_info.server.register_handler("/news/secret_feed.xml", handle_feed)

        db_path = tmp_path / "robots.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sources_file = data_dir / "news_sources.txt"
        sources_file.write_text(f"{feed_url}, Secret Source, General, 30, no\n", encoding="utf-8")

        settings = load_settings(
            None,
            {
                "db_path": str(db_path),
                "allow_loopback_for_tests": "true",
            },
        )

        poller = NewsPoller(settings, mode="fixture")
        report = poller.poll(sources_file=sources_file, force=True)

        assert report.sources_failed == 1
        assert "robots_disallowed" in report.feed_outcomes[feed_url]
        # Invariant: robots-disallowed feed is reported, not fetched
        assert called["fetch"] is False


def test_feed_hosts_outside_allowlist_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "allowlist.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    sources_file = data_dir / "news_sources.txt"
    sources_file.write_text(
        "https://forbidden-news.com/feed.xml, Forbidden, News, 30, no\n", encoding="utf-8"
    )

    settings = load_settings(
        None,
        {
            "db_path": str(db_path),
        },
    )

    poller = NewsPoller(
        settings,
        mode="real",
        allowlist={"allowed-news.com"},
    )
    report = poller.poll(sources_file=sources_file, force=True)

    assert report.sources_failed == 1
    assert "url_not_allowlisted" in report.feed_outcomes["https://forbidden-news.com/feed.xml"]


def test_idempotent_poll_no_duplicates(tmp_path: Path) -> None:
    with running_fixture_server() as server_info:
        feed_url = server_info.url_for("/news/feed.xml")

        rss_content = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<rss version="2.0"><channel><title>Daily Tech</title>'
            b"<item><title>Autonomous Rovers Explore Lunar South Pole</title>"
            b"<link>" + feed_url.encode() + b"#rover</link>"
            b"<description>Robotic exploration underway.</description></item>"
            b"</channel></rss>"
        )

        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_bytes(b"User-agent: *\nAllow: /\n", "text/plain")
        )
        server_info.server.register_handler(
            "/news/feed.xml", lambda h: h._send_bytes(rss_content, "application/rss+xml")
        )

        db_path = tmp_path / "idempotent.db"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        sources_file = data_dir / "news_sources.txt"
        sources_file.write_text(f"{feed_url}, Rover News, Space, 30, no\n", encoding="utf-8")

        settings = load_settings(
            None,
            {
                "db_path": str(db_path),
                "allow_loopback_for_tests": "true",
            },
        )

        poller = NewsPoller(settings, mode="fixture")

        # First run: 1 added
        r1 = poller.poll(sources_file=sources_file, force=True)
        assert r1.items_added == 1
        assert r1.items_updated == 0

        # Second run: 0 added, 1 updated (idempotent, no duplicates)
        poller.scheduler.host_next_allowed.clear()
        r2 = poller.poll(sources_file=sources_file, force=True)
        assert r2.items_added == 0
        assert r2.items_updated == 1

        with Database(db_path) as db, db.reader() as conn:
            item_count = conn.execute("SELECT count(*) FROM news_items").fetchone()[0]
            doc_count = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
            assert item_count == 1
            assert doc_count == 1

            # Consistency check passes
            report = verify_index_consistency(conn)
            assert report.is_consistent


def test_cli_news_poll(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from search_engine.cli import main

    db_path = tmp_path / "cli_news.db"
    sources_file = tmp_path / "empty_sources.txt"
    sources_file.write_text("# Empty sources\n", encoding="utf-8")

    exit_code = main(
        ["news", "poll", "--db", str(db_path), "--sources", str(sources_file), "--json"]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert '"status": "success"' in captured.out
    assert '"sources_polled": 0' in captured.out
