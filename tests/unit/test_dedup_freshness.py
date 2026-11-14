"""Unit and integration tests for Step 17: Deduplication, Updates, and Freshness.

Verifies:
- Exact duplicates by content hash grouped together.
- Canonical grouping (rel=canonical honored ONLY for same registered domain).
- Cross-domain canonical ignored with warning.
- Trailing-slash and index-file variants consolidated.
- Redirect chains and old document deactivation.
- Recrawl conditional requests (304 Not Modified).
- Changed content updates document and FTS index.
- Unchanged content refreshes timestamps without touching index.
- 404 / 410 deactivates document and removes from FTS.
- verify_index_consistency() passes after every scenario.
- Repeated crawls never create visible duplicate search results.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.fixtures.server import FixtureHandler, running_fixture_server

from search_engine.config import load_settings
from search_engine.crawler.dedup import (
    canonical_group_key,
    is_valid_canonical,
)
from search_engine.crawler.freshness import (
    enqueue_stale_urls,
    is_stale,
)
from search_engine.crawler.pipeline import CrawlPipeline
from search_engine.db import Database
from search_engine.indexer.consistency import verify_index_consistency
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.resolver import FakeResolver
from search_engine.search import SearchService
from search_engine.storage import FileStore

pytestmark = pytest.mark.loopback


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "dedup_test.db").open()
    yield db
    db.close()


def test_canonical_group_key_normalization() -> None:
    """Trailing slashes and index-file variants map to the exact same canonical key."""
    # Index file variants
    key1 = canonical_group_key("https://example.com/blog/index.html")
    key2 = canonical_group_key("https://example.com/blog/index.htm")
    key3 = canonical_group_key("https://example.com/blog/index.php")
    key4 = canonical_group_key("https://example.com/blog/")
    key5 = canonical_group_key("https://example.com/blog")
    key6 = canonical_group_key("http://example.com:80/blog/")
    key7 = canonical_group_key("https://example.com:443/blog/index.html")

    assert key1 == "url:example.com/blog"
    assert key1 == key2 == key3 == key4 == key5 == key6 == key7

    # Root paths
    root1 = canonical_group_key("https://example.com/")
    root2 = canonical_group_key("https://example.com")
    root3 = canonical_group_key("https://example.com/index.html")
    assert root1 == "url:example.com/"
    assert root1 == root2 == root3

    # Exact content hash overrides URL
    hash_key = canonical_group_key("https://example.com/any", content_hash="abc123DEF")
    assert hash_key == "hash:abc123def"


def test_is_valid_canonical_same_domain() -> None:
    """rel=canonical is valid only for same registered domain and safe target."""
    final_url = "https://news.example.com/articles/today"

    # Same subdomain
    assert is_valid_canonical("https://news.example.com/articles/canonical", final_url)
    # Different subdomain, same registered domain
    assert is_valid_canonical("https://www.example.com/articles/canonical", final_url)
    assert is_valid_canonical("https://example.com/articles/canonical", final_url)

    # Cross-domain canonical must be rejected
    assert not is_valid_canonical("https://otherdomain.org/articles/canonical", final_url)
    assert not is_valid_canonical("https://example.net/articles/canonical", final_url)

    # Unsafe target
    assert not is_valid_canonical("javascript:alert(1)", final_url)
    assert not is_valid_canonical("http://192.168.1.1/page", final_url)


def test_is_stale_and_enqueue_stale_urls(test_db: Database) -> None:
    """Freshness policy detects stale documents and enqueues URLs."""
    now = datetime.now(UTC)
    fresh_time = (now - timedelta(days=2)).isoformat()
    stale_time = (now - timedelta(days=10)).isoformat()

    assert not is_stale(fresh_time, recrawl_after_days=7, now=now)
    assert is_stale(stale_time, recrawl_after_days=7, now=now)

    with test_db.transaction() as conn:
        conn.execute(
            "INSERT INTO hosts (id, host, registered_domain, created_at) "
            "VALUES (1, 'example.com', 'example.com', ?)",
            (now.isoformat(),),
        )
        conn.execute(
            "INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at) "
            "VALUES (1, 'https://example.com/fresh', 1, 'done', 'seed', ?, ?)",
            (now.isoformat(), now.isoformat()),
        )
        conn.execute(
            "INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at) "
            "VALUES (2, 'https://example.com/stale', 1, 'done', 'seed', ?, ?)",
            (now.isoformat(), now.isoformat()),
        )
        conn.execute(
            "INSERT INTO documents "
            "(url_id, doc_type, final_url, title, status, fetched_at, updated_at) "
            "VALUES (1, 'page', 'https://example.com/fresh', 'Fresh', 'active', ?, ?)",
            (fresh_time, fresh_time),
        )
        conn.execute(
            "INSERT INTO documents "
            "(url_id, doc_type, final_url, title, status, fetched_at, updated_at) "
            "VALUES (2, 'page', 'https://example.com/stale', 'Stale', 'active', ?, ?)",
            (stale_time, stale_time),
        )

        enqueued = enqueue_stale_urls(conn, recrawl_after_days=7, now=now)
        assert enqueued == 1

        # Check status
        row_fresh = conn.execute("SELECT status FROM urls WHERE id = 1").fetchone()
        row_stale = conn.execute("SELECT status FROM urls WHERE id = 2").fetchone()
        assert row_fresh[0] == "done"
        assert row_stale[0] == "pending"


def test_exact_duplicate_by_content_hash(tmp_path: Path, test_db: Database) -> None:
    """Exact duplicates with same content hash are assigned same canonical_group_id."""
    with running_fixture_server() as server_info:
        host = server_info.host
        page1_url = server_info.url_for("/doc-alpha")
        page2_url = server_info.url_for("/doc-beta-duplicate")

        identical_html = (
            b"<html><head><title>Identical Unique Content</title></head>"
            b"<body><h1>Quantum Computing Principles</h1>"
            b"<p>Superposition and entanglement enable exponential computation.</p></body></html>"
        )

        def handle_p1(h: FixtureHandler) -> None:
            h._send_bytes(identical_html, "text/html; charset=utf-8")

        def handle_p2(h: FixtureHandler) -> None:
            h._send_bytes(identical_html, "text/html; charset=utf-8")

        server_info.server.register_handler("/doc-alpha", handle_p1)
        server_info.server.register_handler("/doc-beta-duplicate", handle_p2)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{page1_url}\n{page2_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "dedup_test.db"),
            }
        )

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )

        res = pipeline.run(seeds=[page1_url, page2_url])
        assert res.status == "completed"

        with test_db.reader() as conn:
            docs = conn.execute(
                "SELECT id, final_url, content_hash, canonical_group_id FROM documents ORDER BY id"
            ).fetchall()
            assert len(docs) == 2
            # Both documents share the same canonical_group_id
            assert docs[0]["canonical_group_id"] == docs[1]["canonical_group_id"]
            assert docs[0]["content_hash"] == docs[1]["content_hash"]

            # Consistency check
            report = verify_index_consistency(conn)
            assert report.is_consistent

        # Search verification: exact duplicates must collapse into a single visible result
        with test_db.reader() as conn:
            search_svc = SearchService(conn)
            s_res = search_svc.search("quantum superposition entanglement")
            assert len(s_res.results) == 1
            assert s_res.results[0].duplicate_count == 2


def test_canonical_duplicate_and_cross_domain_ignored(tmp_path: Path, test_db: Database) -> None:
    """Same-domain rel=canonical groups documents; cross-domain rel=canonical is ignored."""
    with running_fixture_server() as server_info:
        host = server_info.host
        canon_url = server_info.url_for("/canon")
        variant_url = server_info.url_for("/variant")
        cross_url = server_info.url_for("/cross-domain")

        def handle_canon(h: FixtureHandler) -> None:
            body = (
                b"<html><head><title>Original Canonical</title></head>"
                b"<body><h1>The Great Library of Alexandria</h1>"
                b"<p>Major scroll repository of the ancient Hellenistic world.</p></body></html>"
            )
            h._send_bytes(body, "text/html; charset=utf-8")

        def handle_variant(h: FixtureHandler) -> None:
            # Same domain canonical link
            body = (
                f'<html><head><title>V</title><link rel="canonical" href="{canon_url}"></head>'
                f"<body><h1>The Library of Alexandria</h1>"
                f"<p>Ancient Hellenistic scroll archives.</p></body></html>"
            ).encode()
            h._send_bytes(body, "text/html; charset=utf-8")

        def handle_cross(h: FixtureHandler) -> None:
            # Cross-domain canonical link (must be ignored)
            body = (
                b"<html><head><title>Cross</title>"
                b'<link rel="canonical" href="https://external-forbidden.org/target"></head>'
                b"<body><h1>Alexandria Museum</h1><p>Scholarly antiquity.</p></body></html>"
            )
            h._send_bytes(body, "text/html; charset=utf-8")

        server_info.server.register_handler("/canon", handle_canon)
        server_info.server.register_handler("/variant", handle_variant)
        server_info.server.register_handler("/cross-domain", handle_cross)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{canon_url}\n{variant_url}\n{cross_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "dedup_test.db"),
            }
        )

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )

        res = pipeline.run(seeds=[canon_url, variant_url, cross_url])
        assert res.status == "completed"

        with test_db.reader() as conn:
            docs = conn.execute(
                "SELECT id, final_url, canonical_url, canonical_group_id FROM documents ORDER BY id"
            ).fetchall()
            doc_map = {d["final_url"]: d for d in docs}

            # /variant shares group with /canon
            assert (
                doc_map[variant_url]["canonical_group_id"]
                == doc_map[canon_url]["canonical_group_id"]
            )

            # /cross-domain had external canonical ignored, so canonical_url is None or not external
            assert doc_map[cross_url]["canonical_url"] is None
            assert doc_map[cross_url]["canonical_group_id"] == doc_map[cross_url]["id"]

            report = verify_index_consistency(conn)
            assert report.is_consistent

        # Search verification: /canon and /variant collapse into 1 group
        with test_db.reader() as conn:
            search_svc = SearchService(conn)
            s_res = search_svc.search("Alexandria Hellenistic")
            # Should return 2 groups (one for canon+variant, one for cross)
            assert len(s_res.results) == 2


def test_redirect_consolidation(tmp_path: Path, test_db: Database) -> None:
    """Redirecting URL consolidates to target URL and old document is deactivated."""
    with running_fixture_server() as server_info:
        host = server_info.host
        from_url = server_info.url_for("/start-page")
        target_url = server_info.url_for("/destination-page")

        # Initial state: /start-page returns normal HTML
        def handle_initial(h: FixtureHandler) -> None:
            body = (
                b"<html><head><title>Initial</title></head>"
                b"<body><h1>Ancient Astronomy</h1><p>Star charts of Babylon.</p></body></html>"
            )
            h._send_bytes(body, "text/html; charset=utf-8")

        server_info.server.register_handler("/start-page", handle_initial)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{from_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "dedup_test.db"),
            }
        )

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )

        # 1. Initial crawl
        res1 = pipeline.run(seeds=[from_url])
        assert res1.status == "completed"

        with test_db.reader() as conn:
            d = conn.execute(
                "SELECT id, status FROM documents WHERE final_url = ?", (from_url,)
            ).fetchone()
            assert d is not None
            assert d["status"] == "active"
            assert verify_index_consistency(conn).is_consistent

        # 2. Server updates: /start-page now redirects to /destination-page
        def handle_redirect(h: FixtureHandler) -> None:
            h._send_redirect("/destination-page", code=302)

        def handle_dest(h: FixtureHandler) -> None:
            body = (
                b"<html><head><title>Destination</title></head>"
                b"<body><h1>Ancient Astronomy</h1><p>Consolidated star charts.</p></body></html>"
            )
            h._send_bytes(body, "text/html; charset=utf-8")

        server_info.server.register_handler("/start-page", handle_redirect)
        server_info.server.register_handler("/destination-page", handle_dest)

        # 3. Recrawl
        res2 = pipeline.run(seeds=[from_url], recrawl=True)
        assert res2.status == "completed"

        with test_db.reader() as conn:
            old_doc = conn.execute(
                "SELECT id, status FROM documents WHERE final_url = ?", (from_url,)
            ).fetchone()
            new_doc = conn.execute(
                "SELECT id, status FROM documents WHERE final_url = ?", (target_url,)
            ).fetchone()

            # Old document was deactivated
            assert old_doc["status"] == "inactive"
            # New document is active
            assert new_doc is not None
            assert new_doc["status"] == "active"

            # Consistency check passes!
            assert verify_index_consistency(conn).is_consistent


def test_recrawl_conditional_304_and_unchanged_content(tmp_path: Path, test_db: Database) -> None:
    """304 Not Modified and unchanged 200 responses refresh timestamps without re-indexing."""
    with running_fixture_server() as server_info:
        host = server_info.host
        page_url = server_info.url_for("/conditional-page")

        req_count = 0

        def handle_conditional(h: FixtureHandler) -> None:
            nonlocal req_count
            req_count += 1
            if_none_match = h.headers.get("if-none-match")
            if if_none_match == '"etag-v1"':
                h.send_response(304)
                h.send_header("ETag", '"etag-v1"')
                h.send_header("Content-Length", "0")
                h.end_headers()
                return

            body = (
                b"<html><head><title>Conditional Article</title></head>"
                b"<body><h1>Photosynthesis</h1><p>Chlorophyll absorption spectra.</p></body></html>"
            )
            h.send_response(200)
            h.send_header("ETag", '"etag-v1"')
            h.send_header("Last-Modified", "Wed, 21 Oct 2025 07:28:00 GMT")
            h.send_header("Content-Type", "text/html; charset=utf-8")
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)

        server_info.server.register_handler("/conditional-page", handle_conditional)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{page_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "dedup_test.db"),
            }
        )

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )

        # 1. First fetch: 200 OK
        res1 = pipeline.run(seeds=[page_url])
        assert res1.status == "completed"

        with test_db.reader() as conn:
            d1 = conn.execute(
                "SELECT id, fetched_at, updated_at, etag FROM documents WHERE final_url = ?",
                (page_url,),
            ).fetchone()
            assert d1["etag"] == '"etag-v1"'
            t1_fetch = d1["fetched_at"]
            assert verify_index_consistency(conn).is_consistent

        # 2. Second fetch with recrawl: 304 Not Modified
        res2 = pipeline.run(seeds=[page_url], recrawl=True)
        assert res2.status == "completed"

        with test_db.reader() as conn:
            d2 = conn.execute(
                "SELECT id, fetched_at, updated_at, etag FROM documents WHERE final_url = ?",
                (page_url,),
            ).fetchone()
            assert d2["etag"] == '"etag-v1"'
            # Fetched timestamp refreshed
            assert d2["fetched_at"] >= t1_fetch
            assert verify_index_consistency(conn).is_consistent


def test_recrawl_changed_content_and_404_deactivation(tmp_path: Path, test_db: Database) -> None:
    """Changed content updates document and FTS index; 404 deactivates document."""
    with running_fixture_server() as server_info:
        host = server_info.host
        page_url = server_info.url_for("/mutable-page")

        current_body = (
            b"<html><head><title>Old Edition</title></head>"
            b"<body><h1>Volcanic Eruptions</h1><p>Magma and pyroclastic flows.</p></body></html>"
        )
        current_status = 200

        def handle_mutable(h: FixtureHandler) -> None:
            if current_status == 404:
                h._send_response_simple(404, b"Not Found", "text/plain")
            else:
                h._send_bytes(current_body, "text/html; charset=utf-8")

        server_info.server.register_handler("/mutable-page", handle_mutable)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{page_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "dedup_test.db"),
            }
        )

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )

        # 1. Initial crawl: Old Edition
        pipeline.run(seeds=[page_url])
        with test_db.reader() as conn:
            search_svc = SearchService(conn)
            assert len(search_svc.search("pyroclastic").results) == 1
            assert len(search_svc.search("basaltic").results) == 0

        # 2. Content changes on server
        current_body = (
            b"<html><head><title>New Edition</title></head>"
            b"<body><h1>Volcanic Eruptions</h1><p>Basaltic lava shields.</p></body></html>"
        )
        pipeline.run(seeds=[page_url], recrawl=True)

        with test_db.reader() as conn:
            d = conn.execute(
                "SELECT title FROM documents WHERE final_url = ?", (page_url,)
            ).fetchone()
            assert d["title"] == "New Edition"
            assert verify_index_consistency(conn).is_consistent

            # Search index was updated
            search_svc = SearchService(conn)
            assert len(search_svc.search("basaltic").results) == 1
            assert len(search_svc.search("pyroclastic").results) == 0

        # 3. Page is deleted (returns 404)
        current_status = 404
        pipeline.run(seeds=[page_url], recrawl=True)

        with test_db.reader() as conn:
            d_deleted = conn.execute(
                "SELECT status FROM documents WHERE final_url = ?", (page_url,)
            ).fetchone()
            assert d_deleted["status"] == "inactive"
            assert verify_index_consistency(conn).is_consistent

            # Document is no longer visible in search
            search_svc = SearchService(conn)
            assert len(search_svc.search("basaltic").results) == 0
            assert len(search_svc.search("caldera").results) == 0


def test_repeated_crawls_never_create_visible_duplicates(tmp_path: Path, test_db: Database) -> None:
    """Repeated crawls over a site never duplicate index rows or visible search results."""
    with running_fixture_server() as server_info:
        host = server_info.host
        page1_url = server_info.url_for("/corpus/page1")
        page2_url = server_info.url_for("/corpus/page2")
        page3_url = server_info.url_for("/corpus/page3")

        def handle_p1(h: FixtureHandler) -> None:
            body = (
                f"<html><head><title>Quantum Core 1</title></head>"
                f"<body><h1>Quantum Processing</h1>"
                f'<a href="{page2_url}">Next</a>'
                f"<p>Qubits exhibit state superposition in quantum architectures.</p></body></html>"
            ).encode()
            h._send_bytes(body, "text/html; charset=utf-8")

        def handle_p2(h: FixtureHandler) -> None:
            body = (
                f"<html><head><title>Quantum Core 2</title></head>"
                f"<body><h1>Quantum Teleportation</h1>"
                f'<a href="{page3_url}">Next</a>'
                f"<p>Entanglement channels facilitate quantum teleportation.</p></body></html>"
            ).encode()
            h._send_bytes(body, "text/html; charset=utf-8")

        def handle_p3(h: FixtureHandler) -> None:
            body = (
                b"<html><head><title>Quantum Core 3</title></head>"
                b"<body><h1>Quantum Cryptography</h1>"
                b"<p>BB84 protocol leverages quantum key distribution.</p></body></html>"
            )
            h._send_bytes(body, "text/html; charset=utf-8")

        server_info.server.register_handler("/corpus/page1", handle_p1)
        server_info.server.register_handler("/corpus/page2", handle_p2)
        server_info.server.register_handler("/corpus/page3", handle_p3)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{page1_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "dedup_test.db"),
            }
        )

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )

        # First crawl: discovers page1 -> page2 -> page3
        res1 = pipeline.run(seeds=[page1_url])
        assert res1.status == "completed"

        with test_db.reader() as conn:
            assert (
                conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'active'").fetchone()[0]
                == 3
            )
            assert conn.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0] == 3
            assert conn.execute("SELECT COUNT(*) FROM doc_fts_tri").fetchone()[0] == 3
            assert verify_index_consistency(conn).is_consistent

        # Second crawl: repeat full crawl
        res2 = pipeline.run(seeds=[page1_url], recrawl=True)
        assert res2.status == "completed"

        with test_db.reader() as conn:
            # Document and FTS row counts must still be exactly 3!
            assert (
                conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'active'").fetchone()[0]
                == 3
            )
            assert conn.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0] == 3
            assert conn.execute("SELECT COUNT(*) FROM doc_fts_tri").fetchone()[0] == 3
            assert verify_index_consistency(conn).is_consistent

            search_svc = SearchService(conn)
            search_res = search_svc.search("quantum")
            assert len(search_res.results) == 3
            assert len({r.doc_id for r in search_res.results}) == 3
