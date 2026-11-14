"""Unit and integration tests for the bounded crawl pipeline (Step 16, decision D8, D12, D13)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from tests.fixtures.server import running_fixture_server

from search_engine.config import load_settings
from search_engine.crawler.pipeline import CrawlPipeline
from search_engine.crawler.preflight import (
    create_preflight_approval,
    verify_preflight_approval,
)
from search_engine.db import Database
from search_engine.errors import PolicyDenied
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.resolver import FakeResolver
from search_engine.reporting.events import Reason
from search_engine.search import SearchService
from search_engine.storage import FileStore

pytestmark = pytest.mark.loopback


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "crawl_test.db").open()
    yield db
    db.close()


def test_crawl_pipeline_end_to_end_with_robots_and_search(
    tmp_path: Path, test_db: Database
) -> None:
    with running_fixture_server() as server_info:
        host = server_info.host

        # Configure custom routes on fixture server
        root_url = server_info.url_for("/")
        page2_url = server_info.url_for("/page2")
        disallowed_url = server_info.url_for("/disallowed")

        def handle_root(h: object) -> None:
            body = (
                f"<html><head><title>Root Gateway</title></head>"
                f"<body><h1>Welcome</h1>"
                f'<a href="{page2_url}">Page Two</a>'
                f'<a href="{disallowed_url}">Secret Disallowed</a>'
                f"</body></html>"
            ).encode()
            h._send_bytes(body, "text/html; charset=utf-8")  # type: ignore[attr-defined]

        def handle_page2(h: object) -> None:
            body = (
                b"<html><head><title>Second Article</title></head>"
                b"<body><p>Detailed knowledge content for index verification.</p></body></html>"
            )
            h._send_bytes(body, "text/html; charset=utf-8")  # type: ignore[attr-defined]

        def handle_robots(h: object) -> None:
            body = b"User-agent: *\nDisallow: /disallowed\nAllow: /\n"
            h._send_bytes(body, "text/plain")  # type: ignore[attr-defined]

        server_info.server.register_handler("/", handle_root)
        server_info.server.register_handler("/page2", handle_page2)
        server_info.server.register_handler("/robots.txt", handle_robots)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{root_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "crawl_test.db"),
            }
        )

        resolver = FakeResolver(table={host: ["127.0.0.1"]})
        transport = HttpxTransport(allow_loopback=True)
        file_store = FileStore(data_dir)

        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=file_store,
            resolver=resolver,
            transport=transport,
        )

        res = pipeline.run(seeds=[root_url])
        assert res.status == "completed"
        assert res.pages_stored == 2  # Root and Page2

        # Verify report counts
        report = res.report
        assert report.count(Reason.STORED) == 2
        assert report.count(Reason.BLOCKED_ROBOTS) == 1

        # Check search service returns both crawled documents
        with test_db.reader() as conn:
            service = SearchService(conn)
            search_res = service.search("Gateway")
            assert len(search_res.results) == 1
            assert search_res.results[0].title == "Root Gateway"

            search_res2 = service.search("Detailed knowledge")
            assert len(search_res2.results) == 1
            assert search_res2.results[0].title == "Second Article"


def test_redirect_to_disallowed_target_is_blocked(tmp_path: Path, test_db: Database) -> None:
    with running_fixture_server() as server_info:
        host = server_info.host
        redir_url = server_info.url_for("/redirect-start")
        target_url = server_info.url_for("/secret-target")

        def handle_redir(h: object) -> None:
            h._send_redirect(target_url)  # type: ignore[attr-defined]

        def handle_robots(h: object) -> None:
            body = b"User-agent: *\nDisallow: /secret-target\nAllow: /\n"
            h._send_bytes(body, "text/plain")  # type: ignore[attr-defined]

        server_info.server.register_handler("/redirect-start", handle_redir)
        server_info.server.register_handler("/robots.txt", handle_robots)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{redir_url}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": True,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "crawl_test.db"),
            }
        )

        resolver = FakeResolver(table={host: ["127.0.0.1"]})
        pipeline = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=resolver,
            transport=HttpxTransport(allow_loopback=True),
        )

        res = pipeline.run(seeds=[redir_url])
        assert res.status == "completed"
        # The redirect target was evaluated by scheduler and blocked by robots
        assert res.report.count(Reason.BLOCKED_ROBOTS) == 1
        assert res.pages_stored == 0


def test_page_limit_stops_crawl(tmp_path: Path, test_db: Database) -> None:
    with running_fixture_server() as server_info:
        host = server_info.host
        u1 = server_info.url_for("/p1")
        u2 = server_info.url_for("/p2")
        u3 = server_info.url_for("/p3")

        def h1(h: object) -> None:
            body = f'<html><body><a href="{u2}">p2</a><a href="{u3}">p3</a></body></html>'.encode()
            h._send_bytes(body, "text/html")  # type: ignore[attr-defined]

        def h2(h: object) -> None:
            h._send_bytes(b"<html><body>page 2</body></html>", "text/html")  # type: ignore[attr-defined]

        def h3(h: object) -> None:
            h._send_bytes(b"<html><body>page 3</body></html>", "text/html")  # type: ignore[attr-defined]

        server_info.server.register_handler("/p1", h1)
        server_info.server.register_handler("/p2", h2)
        server_info.server.register_handler("/p3", h3)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{u1}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": False,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "crawl_test.db"),
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

        res = pipeline.run(seeds=[u1], max_pages=2)
        assert res.status == "page_limit"
        assert res.pages_stored == 2


def test_mixed_content_types_and_noindex(tmp_path: Path, test_db: Database) -> None:
    with running_fixture_server() as server_info:
        host = server_info.host
        u_html = server_info.url_for("/html")
        u_text = server_info.url_for("/text.txt")
        u_noindex = server_info.url_for("/noindex")

        def h_html(h: object) -> None:
            body = (
                f'<html><body><a href="{u_text}">text</a>'
                f'<a href="{u_noindex}">noindex</a></body></html>'
            ).encode()
            h._send_bytes(body, "text/html")  # type: ignore[attr-defined]

        def h_text(h: object) -> None:
            h._send_bytes(b"Plain text file content.", "text/plain")  # type: ignore[attr-defined]

        def h_noindex(h: object) -> None:
            body = (
                b'<html><head><meta name="robots" content="noindex"></head>'
                b"<body>Do not index this content.</body></html>"
            )
            h._send_bytes(body, "text/html")  # type: ignore[attr-defined]

        server_info.server.register_handler("/html", h_html)
        server_info.server.register_handler("/text.txt", h_text)
        server_info.server.register_handler("/noindex", h_noindex)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{u_html}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": False,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "crawl_test.db"),
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

        res = pipeline.run(seeds=[u_html])
        assert res.status == "completed"
        assert res.report.count(Reason.STORED) == 2  # html + text
        assert res.report.count(Reason.NOINDEX) == 1  # noindex page

        with test_db.reader() as conn:
            # Verify noindex page is stored in documents table but not indexed in FTS
            sql = (
                "SELECT status FROM documents "
                "WHERE url_id = (SELECT id FROM urls WHERE url_key = ?)"
            )
            row = conn.execute(sql, (u_noindex,)).fetchone()
            assert row is not None
            assert row[0] == "noindex"

            # Search cannot find noindex content
            service = SearchService(conn)
            assert len(service.search("Do not index").results) == 0
            # Search CAN find text file content
            text_results = service.search('"Plain text file"').results
            assert len(text_results) == 1
            assert text_results[0].url == u_text


def test_stop_file_halts_crawl(tmp_path: Path, test_db: Database) -> None:
    with running_fixture_server() as server_info:
        host = server_info.host
        u_start = server_info.url_for("/start")

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{u_start}\n", encoding="utf-8")

        # Create STOP file in data directory
        stop_file = data_dir / "STOP"
        stop_file.write_text("STOP NOW", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": False,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "crawl_test.db"),
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

        res = pipeline.run(seeds=[u_start])
        assert res.status == "stop_file"
        assert res.pages_stored == 0


def test_resume_preserves_work_without_duplication(tmp_path: Path, test_db: Database) -> None:
    with running_fixture_server() as server_info:
        host = server_info.host
        u1 = server_info.url_for("/p1")
        u2 = server_info.url_for("/p2")

        def h1(h: object) -> None:
            body = f'<html><body><a href="{u2}">p2</a></body></html>'.encode()
            h._send_bytes(body, "text/html")  # type: ignore[attr-defined]

        def h2(h: object) -> None:
            h._send_bytes(b"<html><body>page 2</body></html>", "text/html")  # type: ignore[attr-defined]

        server_info.server.register_handler("/p1", h1)
        server_info.server.register_handler("/p2", h2)

        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        seeds_file = data_dir / "seeds.txt"
        seeds_file.write_text(f"{u1}\n", encoding="utf-8")

        settings = load_settings(
            overrides={
                "allow_loopback_for_tests": True,
                "allowlist_mode": False,
                "seeds_file": str(seeds_file),
                "db_path": str(tmp_path / "crawl_test.db"),
            }
        )

        # Run 1: max_pages=1 (crawls only p1)
        pipeline1 = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )
        res1 = pipeline1.run(seeds=[u1], max_pages=1)
        assert res1.status == "page_limit"
        assert res1.pages_stored == 1

        # Run 2: resume with no seeds argument (picks up remaining pending URLs)
        pipeline2 = CrawlPipeline(
            settings,
            mode="fixture",
            db=test_db,
            file_store=FileStore(data_dir),
            resolver=FakeResolver(table={host: ["127.0.0.1"]}),
            transport=HttpxTransport(allow_loopback=True),
        )
        res2 = pipeline2.run(seeds=None, max_pages=10)
        assert res2.status == "completed"
        assert res2.pages_stored == 1  # only p2 was stored on second run

        with test_db.reader() as conn:
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_real_mode_preflight_authorization(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    seeds_file = data_dir / "seeds.txt"
    seeds_file.write_text("https://example.com/\n", encoding="utf-8")

    # 1. Real network disabled -> refuses
    settings = load_settings(
        overrides={
            "real_network_enabled": False,
            "seeds_file": str(seeds_file),
            "db_path": str(tmp_path / "test.db"),
        }
    )
    with pytest.raises(PolicyDenied, match="real network is disabled"):
        verify_preflight_approval(settings)

    # 2. Real network enabled but preflight missing -> refuses
    settings_enabled = load_settings(
        overrides={
            "real_network_enabled": True,
            "seeds_file": str(seeds_file),
            "db_path": str(tmp_path / "test.db"),
        }
    )
    with pytest.raises(PolicyDenied, match="preflight approval file not found"):
        verify_preflight_approval(settings_enabled)

    # 3. Create preflight approval -> passes
    approved_file = create_preflight_approval(settings_enabled)
    assert approved_file.exists()
    verify_preflight_approval(settings_enabled)

    # 4. Modify seeds file -> hash mismatch -> refuses
    seeds_file.write_text("https://example.com/\nhttps://tampered.org/\n", encoding="utf-8")
    with pytest.raises(PolicyDenied, match="does not match current"):
        verify_preflight_approval(settings_enabled)


def test_disk_space_guard_halts_crawl(tmp_path: Path, test_db: Database) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    seeds_file = data_dir / "seeds.txt"
    seeds_file.write_text("http://127.0.0.1:9999/\n", encoding="utf-8")

    settings = load_settings(
        overrides={
            "allow_loopback_for_tests": True,
            "allowlist_mode": False,
            "seeds_file": str(seeds_file),
            "db_path": str(tmp_path / "crawl_test.db"),
        }
    )

    # Require an impossibly large amount of free disk space (e.g. 100 Petabytes)
    impossibly_large_bytes = 100 * 1024 * 1024 * 1024 * 1024 * 1024
    pipeline = CrawlPipeline(
        settings,
        mode="fixture",
        db=test_db,
        file_store=FileStore(data_dir),
        resolver=FakeResolver(),
        transport=HttpxTransport(allow_loopback=True),
        min_free_disk_bytes=impossibly_large_bytes,
    )

    res = pipeline.run(seeds=["http://127.0.0.1:9999/"])
    assert res.status == "disk_low"
    assert res.report.count(Reason.FAILED) == 1
