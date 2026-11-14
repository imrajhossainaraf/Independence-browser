"""Unit tests for Operator Controls and Policy Documents (Step 25)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from search_engine.cli import main
from search_engine.config import load_settings
from search_engine.db import Database
from search_engine.indexer.build import upsert_document_index
from search_engine.operations.controls import (
    block_target,
    check_engine_status,
    disable_news_source,
    inspect_drive_and_permissions,
    list_blocks,
    pause_crawler,
    remove_document,
    remove_entity,
    remove_news_item,
    resume_crawler,
    stop_crawler,
    takedown_image,
    unblock_target,
)
from search_engine.operations.retention import apply_retention_purge
from search_engine.search.service import SearchService
from search_engine.storage import FileStore


def _setup_test_environment(
    tmp_path: Path,
) -> tuple[Database, FileStore, Path, Path, Path, datetime]:
    """Helper creating an initialized database and file store."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    data_dir = tmp_path / "data"
    db_path = data_dir / "index" / "search.db"
    raw_dir = data_dir / "raw"
    image_dir = data_dir / "images"

    store = FileStore(data_dir)
    db = Database(db_path).open()
    return db, store, data_dir, raw_dir, image_dir, now


def test_block_and_unblock_host_domain_prefix(tmp_path: Path) -> None:
    """Proves block and unblock immediately update URLs and deindex matching documents."""
    db, _, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()

    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO hosts (id, host, registered_domain, created_at) VALUES "
                "(1, 'spam.example.com', 'example.com', ?), "
                "(2, 'good.example.com', 'example.com', ?), "
                "(3, 'other.org', 'other.org', ?)",
                (now_iso, now_iso, now_iso),
            )
            conn.execute(
                """
                INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at)
                VALUES
                (1, 'https://spam.example.com/ad', 1, 'done', 'seed', ?, ?),
                (2, 'https://good.example.com/page1', 2, 'done', 'seed', ?, ?),
                (3, 'https://other.org/article', 3, 'done', 'seed', ?, ?)
                """,
                (now_iso, now_iso, now_iso, now_iso, now_iso, now_iso),
            )
            # Add documents
            body = "Spam advertisement content to be removed."
            conn.execute(
                """
                INSERT INTO documents (
                    id, url_id, doc_type, final_url, canonical_url, title,
                    text_gz, word_count, status, fetched_at, updated_at
                )
                VALUES (1, 1, 'page', 'https://spam.example.com/ad', 'https://spam.example.com/ad',
                        'Spam Title', ?, 6, 'active', ?, ?)
                """,
                (gzip.compress(body.encode("utf-8")), now_iso, now_iso),
            )
            upsert_document_index(conn, 1)

        # Verify initial index state
        with db.reader() as conn:
            assert conn.execute("SELECT COUNT(*) FROM doc_fts WHERE rowid = 1").fetchone()[0] == 1

        # 1. Block by host
        rec = block_target(db, "spam.example.com", "host", reason="spam_report")
        assert rec.kind == "host"
        assert rec.target == "spam.example.com"

        # Check document marked removed and deleted from FTS
        with db.reader() as conn:
            doc_row = conn.execute("SELECT status FROM documents WHERE id = 1").fetchone()
            assert doc_row[0] == "removed"
            assert conn.execute("SELECT COUNT(*) FROM doc_fts WHERE rowid = 1").fetchone()[0] == 0
            url_row = conn.execute("SELECT status, last_reason FROM urls WHERE id = 1").fetchone()
            assert url_row[0] == "blocked"
            assert url_row[1] == "spam_report"

        # 2. Block by domain
        block_target(db, "other.org", "domain")
        with db.reader() as conn:
            url_row3 = conn.execute("SELECT status FROM urls WHERE id = 3").fetchone()
            assert url_row3[0] == "blocked"

        # 3. List blocks
        blocks = list_blocks(db)
        assert len(blocks) == 2

        # 4. Unblock
        assert unblock_target(db, "spam.example.com", "host") is True
        assert len(list_blocks(db)) == 1

    finally:
        db.close()


def test_remove_document_by_url_and_purge_raw(tmp_path: Path) -> None:
    """Document removal by exact URL deindexes and optionally purges unreferenced raw file."""
    db, store, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()

    try:
        # Create raw file
        raw_bytes = b"<html><body>Page content to delete</body></html>"
        raw_hash = store.put_raw(raw_bytes)
        raw_file = store.raw_path(raw_hash)
        assert raw_file.exists()

        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO hosts (id, host, registered_domain, created_at) "
                "VALUES (1, 'doc.example.com', 'example.com', ?)",
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at)
                VALUES (1, 'https://doc.example.com/p1', 1, 'done', 'seed', ?, ?)
                """,
                (now_iso, now_iso),
            )
            cur = conn.execute(
                """
                INSERT INTO documents (
                    url_id, doc_type, final_url, canonical_url, title,
                    text_gz, word_count, raw_hash, status, fetched_at, updated_at
                )
                VALUES (1, 'page', 'https://doc.example.com/p1', 'https://doc.example.com/p1',
                        'Page One', ?, 5, ?, 'active', ?, ?)
                """,
                (gzip.compress(b"Page content to delete"), raw_hash, now_iso, now_iso),
            )
            doc_id = int(cur.lastrowid or 0)
            upsert_document_index(conn, doc_id)

        # Remove document with purge_raw=True
        rep = remove_document(
            db,
            store=store,
            url="https://doc.example.com/p1",
            purge_raw=True,
            reason="dmca_takedown",
        )
        assert rep.documents_removed == 1
        assert rep.raw_files_purged == 1

        # Verify raw file is deleted from disk
        assert not raw_file.exists()

        # Verify search returns 0 results
        settings = load_settings(overrides={"db_path": str(db.path)})
        with db.reader() as conn:
            svc = SearchService(conn, settings=settings)
            res = svc.search("content to delete")
            assert len(res.results) == 0

    finally:
        db.close()


def test_remove_document_by_domain(tmp_path: Path) -> None:
    """Document removal by registered domain removes all matching documents."""
    db, _, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()

    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO hosts (id, host, registered_domain, created_at) "
                "VALUES (1, 'sub.target-domain.com', 'target-domain.com', ?)",
                (now_iso,),
            )
            conn.execute(
                """
                INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at)
                VALUES (1, 'https://sub.target-domain.com/a', 1, 'done', 'seed', ?, ?)
                """,
                (now_iso, now_iso),
            )
            cur = conn.execute(
                """
                INSERT INTO documents (
                    url_id, doc_type, final_url, title, text_gz, status, fetched_at, updated_at
                )
                VALUES (
                    1, 'page', 'https://sub.target-domain.com/a', 'Domain Page',
                    ?, 'active', ?, ?
                )
                """,
                (gzip.compress(b"Domain page content"), now_iso, now_iso),
            )
            upsert_document_index(conn, int(cur.lastrowid or 0))

        rep = remove_document(db, domain="target-domain.com")
        assert rep.documents_removed == 1

        with db.reader() as conn:
            status = conn.execute("SELECT status FROM documents WHERE id = 1").fetchone()[0]
            assert status == "removed"

    finally:
        db.close()


def test_disable_news_source_and_remove_item(tmp_path: Path) -> None:
    """Disabling a news source deactivates all its items and deindexes them from FTS."""
    db, _, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()

    try:
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO news_sources (
                    id, feed_url_key, name, category, poll_interval_minutes, enabled
                )
                VALUES (1, 'https://news.example/feed', 'News Outlet', 'General', 30, 1)
                """,
            )
            conn.execute(
                """
                INSERT INTO news_items (
                    id, source_id, link, title, summary, published_at, category,
                    status, first_seen_at, updated_at
                )
                VALUES ('item-1', 1, 'https://news.example/story1', 'Story Headline',
                        'Story Summary', ?, 'General', 'active', ?, ?)
                """,
                (now_iso, now_iso, now_iso),
            )
            cur = conn.execute(
                """
                INSERT INTO documents (
                    news_item_id, doc_type, final_url, title, description, text_gz,
                    status, fetched_at, updated_at
                )
                VALUES ('item-1', 'news', 'https://news.example/story1', 'Story Headline',
                        'Story Summary', ?, 'active', ?, ?)
                """,
                (gzip.compress(b"Story Summary"), now_iso, now_iso),
            )
            upsert_document_index(conn, int(cur.lastrowid or 0))

        # Disable source
        ok = disable_news_source(db, "News Outlet")
        assert ok is True

        with db.reader() as conn:
            src_enabled = conn.execute("SELECT enabled FROM news_sources WHERE id = 1").fetchone()[
                0
            ]
            assert src_enabled == 0
            item_status = conn.execute(
                "SELECT status FROM news_items WHERE id = 'item-1'"
            ).fetchone()[0]
            assert item_status == "inactive"
            assert conn.execute("SELECT COUNT(*) FROM doc_fts WHERE rowid = 1").fetchone()[0] == 0

        # Remove single news item
        assert remove_news_item(db, "item-1") is True

    finally:
        db.close()


def test_image_takedown_deletes_files(tmp_path: Path) -> None:
    """Takedown of an image marks it removed in database and purges files from FileStore."""
    db, store, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()

    try:
        img_bytes = b"RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00" + b"\x00" * 24
        img_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        p160 = store.put_image(img_hash, 160, img_bytes)
        p480 = store.put_image(img_hash, 480, img_bytes)
        assert p160.exists()
        assert p480.exists()

        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO images (
                    id, source_url, page_url, content_hash, width, height, bytes,
                    alt_text, license, attribution, fetched_at, status
                )
                VALUES ('img_take', 'https://example/img.webp', 'https://example/p',
                        ?, 160, 160, 40, 'Logo', 'CC0', 'Author', ?, 'active')
                """,
                (img_hash, now_iso),
            )

        ok = takedown_image(db, store, "img_take", reason="copyright_claim")
        assert ok is True

        with db.reader() as conn:
            row = conn.execute("SELECT status FROM images WHERE id = 'img_take'").fetchone()
            assert row[0] == "removed"

        # Verify files are deleted from disk
        assert not p160.exists()
        assert not p480.exists()

    finally:
        db.close()


def test_remove_entity(tmp_path: Path) -> None:
    """Entity removal deletes from entities and cascades to aliases and facts."""
    db, _, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()

    try:
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO entities (
                    id, name, type, summary, source_url, license, attribution, retrieved_at
                )
                VALUES (1, 'Alan Turing', 'Person', 'Computer scientist.',
                        'https://en.wikipedia.org/wiki/Alan_Turing', 'CC BY-SA 4.0', 'Wikipedia', ?)
                """,
                (now_iso,),
            )
            conn.execute(
                "INSERT INTO entity_aliases (entity_id, alias_norm) VALUES (1, 'turing')",
            )
            conn.execute(
                "INSERT INTO entity_facts (entity_id, position, label, value) "
                "VALUES (1, 0, 'Born', '23 June 1912')",
            )

        assert remove_entity(db, 1) is True

        with db.reader() as conn:
            assert conn.execute("SELECT COUNT(*) FROM entities WHERE id = 1").fetchone()[0] == 0
            assert (
                conn.execute("SELECT COUNT(*) FROM entity_aliases WHERE entity_id = 1").fetchone()[
                    0
                ]
                == 0
            )
            assert (
                conn.execute("SELECT COUNT(*) FROM entity_facts WHERE entity_id = 1").fetchone()[0]
                == 0
            )

    finally:
        db.close()


def test_pause_resume_stop_crawler(tmp_path: Path) -> None:
    """Tests PAUSE and STOP file creation and removal."""
    data_dir = tmp_path / "crawl_ctrl"
    pause_f = pause_crawler(data_dir)
    assert pause_f.is_file()

    assert resume_crawler(data_dir) is True
    assert not pause_f.exists()

    stop_f = stop_crawler(data_dir)
    assert stop_f.is_file()


def test_inspect_drive_and_permissions(tmp_path: Path) -> None:
    """Drive inspections warn on UNC paths or cloud-synced directory names."""
    # Cloud sync folder check
    cloud_dir = tmp_path / "OneDrive - Personal" / "search_data"
    cloud_dir.mkdir(parents=True, exist_ok=True)
    warnings = inspect_drive_and_permissions(cloud_dir)
    assert any("cloud-synchronized" in w for w in warnings)

    # Local normal directory
    normal_dir = tmp_path / "local_data"
    normal_dir.mkdir(parents=True, exist_ok=True)
    warns = inspect_drive_and_permissions(normal_dir)
    assert not any("cloud-synchronized" in w for w in warns)


def test_check_engine_status(tmp_path: Path) -> None:
    """Engine status report reflects operational flags and records."""
    db, _, data_dir, _, _, _ = _setup_test_environment(tmp_path)
    try:
        settings = load_settings(overrides={"db_path": str(db.path)})
        rep = check_engine_status(settings, db)
        assert rep.status in {"healthy", "warning"}
        assert rep.paused is False
        assert rep.stopped is False

        # Create PAUSE file
        pause_crawler(data_dir)
        rep_p = check_engine_status(settings, db)
        assert rep_p.paused is True
        assert rep_p.status == "paused"

        # Create STOP file
        stop_crawler(data_dir)
        rep_s = check_engine_status(settings, db)
        assert rep_s.stopped is True
        assert rep_s.status == "stopped"

    finally:
        db.close()


def test_retention_purge(tmp_path: Path) -> None:
    """Retention purge deactivates news older than 14 days and enforces hard cap."""
    db, _, _, _, _, now = _setup_test_environment(tmp_path)
    now_iso = now.isoformat()
    t_20d_ago = (now - timedelta(days=20)).isoformat()

    try:
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO news_sources (
                    id, feed_url_key, name, category, poll_interval_minutes, enabled
                )
                VALUES (1, 'https://feed.test/rss', 'Feed Test', 'General', 30, 1)
                """,
            )
            # Add an expired item (20 days old)
            conn.execute(
                """
                INSERT INTO news_items (
                    id, source_id, link, title, summary, published_at, category,
                    status, first_seen_at, updated_at
                )
                VALUES ('item-expired', 1, 'https://feed.test/old', 'Old News', 'Old',
                        ?, 'General', 'active', ?, ?)
                """,
                (t_20d_ago, t_20d_ago, t_20d_ago),
            )
            # Add 3 fresh items
            for i in range(1, 4):
                conn.execute(
                    """
                    INSERT INTO news_items (
                        id, source_id, link, title, summary, published_at, category,
                        status, first_seen_at, updated_at
                    )
                    VALUES (?, 1, ?, 'Fresh News', 'Summary', ?, 'General', 'active', ?, ?)
                    """,
                    (f"item-fresh-{i}", f"https://feed.test/fresh{i}", now_iso, now_iso, now_iso),
                )

        # Purge with cap of 2 items
        rep = apply_retention_purge(
            db,
            news_retention_days=14,
            news_max_items=2,
            now=now,
        )
        assert rep.news_items_expired == 1
        assert rep.news_items_pruned_for_cap == 1

        with db.reader() as conn:
            active_count = conn.execute(
                "SELECT COUNT(*) FROM news_items WHERE status = 'active'"
            ).fetchone()[0]
            assert active_count == 2

    finally:
        db.close()


def test_cli_operator_controls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test CLI invocations for operator commands."""
    db, _, _, _, _, _ = _setup_test_environment(tmp_path)
    db_path = str(db.path)
    db.close()

    # Block
    rc = main(["block", "domain", "badsite.com", "--db", db_path])
    assert rc == 0
    assert "Blocked domain: badsite.com" in capsys.readouterr().out

    # Blocks list
    rc = main(["blocks", "--db", db_path, "--json"])
    assert rc == 0
    blocks_data = json.loads(capsys.readouterr().out)
    assert len(blocks_data) == 1

    # Unblock
    rc = main(["unblock", "domain", "badsite.com", "--db", db_path])
    assert rc == 0
    assert "Unblocked domain: badsite.com" in capsys.readouterr().out

    # Status
    rc = main(["status", "--db", db_path, "--json"])
    assert rc == 0
    stat_data = json.loads(capsys.readouterr().out)
    assert "free_disk_bytes" in stat_data

    # Pause and resume
    data_dir = str(tmp_path / "data")
    assert main(["pause", "--data-dir", data_dir]) == 0
    capsys.readouterr()
    assert main(["resume", "--data-dir", data_dir]) == 0
    capsys.readouterr()
    assert main(["stop", "--data-dir", data_dir]) == 0
    capsys.readouterr()

    # Retain purge
    assert main(["retain", "purge", "--db", db_path, "--json"]) == 0
    retain_out = json.loads(capsys.readouterr().out)
    assert "news_items_expired" in retain_out
