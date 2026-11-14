"""Unit tests for Backup, Restore, and Offline Operation (Step 24 / Decision D11)."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from search_engine.cli import main
from search_engine.config import Settings, load_settings
from search_engine.db import Database, connect
from search_engine.errors import StorageError
from search_engine.experience.home import HomeService
from search_engine.indexer.build import upsert_document_index
from search_engine.operations.backup import (
    compute_sha256,
    create_backup,
    restore_backup,
    verify_backup,
)
from search_engine.search.service import SearchService
from search_engine.storage import FileStore


def _populate_sample_store(
    db: Database, raw_dir: Path, image_dir: Path, now: datetime
) -> tuple[int, int, str]:
    """Create sample documents, news items, raw files, and image files for backup testing."""
    now_iso = now.isoformat()
    store = FileStore(raw_dir.parent)

    # 1. Store raw page
    raw_content = (
        b"<html><head><title>Offline Search</title></head>"
        b"<body>Self-hosted search engine.</body></html>"
    )
    raw_hash = store.put_raw(raw_content)

    # 2. Store image thumbnail
    img_bytes = b"RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00" + b"\x00" * 24
    img_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    store.put_image(img_hash, 160, img_bytes)

    # 3. Populate database
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO hosts (id, host, registered_domain, created_at) "
            "VALUES (1, 'offline.example', 'example', ?)",
            (now_iso,),
        )
        conn.execute(
            "INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at) "
            "VALUES (1, 'https://offline.example/page', 1, 'done', 'seed', ?, ?)",
            (now_iso, now_iso),
        )
        body = (
            "Independent search engines provide true self-hosted retrieval "
            "without third party services. Offline data storage allows instant local searching."
        )
        cur = conn.execute(
            """
            INSERT INTO documents (
                url_id, doc_type, final_url, canonical_url, title,
                text_gz, word_count, raw_hash, published_at, fetched_at, status, updated_at
            )
            VALUES (1, 'page', 'https://offline.example/page', 'https://offline.example/page',
                    'Independent Offline Search Architecture', ?, 19, ?, ?, ?, 'active', ?)
            """,
            (gzip.compress(body.encode("utf-8")), raw_hash, now_iso, now_iso, now_iso),
        )
        doc_id = int(cur.lastrowid or 0)
        upsert_document_index(conn, doc_id)

        # News source and item
        conn.execute(
            """
            INSERT INTO news_sources (
                id, feed_url_key, name, category, poll_interval_minutes, last_success_at
            )
            VALUES (1, 'https://offline.example/feed', 'Offline Gazette', 'General', 30, ?)
            """,
            (now_iso,),
        )
        conn.execute(
            """
            INSERT INTO news_items (
                id, source_id, link, title, summary, published_at, category, status,
                first_seen_at, updated_at
            )
            VALUES ('news-1', 1, 'https://offline.example/news1', 'Offline Mode Released',
                    'A new offline mode guarantees self-hosted operation with sockets blocked.',
                    ?, 'General', 'active', ?, ?)
            """,
            (now_iso, now_iso, now_iso),
        )

        # Image record
        conn.execute(
            """
            INSERT INTO images (
                id, source_url, page_url, content_hash, width, height, bytes,
                alt_text, license, attribution, fetched_at, status
            )
            VALUES ('img_1', 'https://offline.example/logo.webp', 'https://offline.example/page',
                    ?, 160, 160, 40, 'Logo', 'CC0', 'Author', ?, 'active')
            """,
            (img_hash, now_iso),
        )

    return doc_id, 1, raw_hash


def test_backup_and_restore_cycle(tmp_path: Path) -> None:
    """Full backup and restore: proves data, index, and files restore identically."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    source_dir = tmp_path / "source_data"
    db_path = source_dir / "index" / "search.db"
    raw_dir = source_dir / "raw"
    image_dir = source_dir / "images"

    db = Database(db_path).open()
    try:
        _populate_sample_store(db, raw_dir, image_dir, now)

        # Pre-backup query baseline
        settings_src = load_settings(overrides={"db_path": str(db_path)})
        with db.reader() as conn:
            search_src = SearchService(conn, settings=settings_src)
            res_src = search_src.search("offline search")
            assert len(res_src.results) > 0

            home_src = HomeService(settings=settings_src)
            payload_src = home_src.get_home(conn, now=now)
            assert len(payload_src.latest) > 0

        # Create online backup
        backup_dir = tmp_path / "backup_archive"
        manifest = create_backup(
            db_path=db_path,
            output_dir=backup_dir,
            raw_dir=raw_dir,
            image_dir=image_dir,
        )
        assert manifest.manifest_version == 1
        assert manifest.total_files > 0
        assert manifest.total_bytes > 0
        assert (backup_dir / "manifest.json").is_file()
        assert (backup_dir / "database.db").is_file()

        # Verify backup standalone function
        verified_manifest = verify_backup(backup_dir)
        assert verified_manifest.database.sha256 == manifest.database.sha256

        # Restore into empty directory
        target_dir = tmp_path / "restored_data"
        result = restore_backup(backup_dir=backup_dir, target_dir=target_dir)

        assert result.integrity_ok is True
        assert result.consistency.is_consistent is True
        assert result.files_restored == manifest.total_files
        assert (target_dir / "index" / "search.db").is_file()

        # Connect to restored directory and verify identical query outputs
        db_restored = Database(target_dir / "index" / "search.db").open()
        try:
            settings_rst = load_settings(
                overrides={"db_path": str(target_dir / "index" / "search.db")}
            )
            with db_restored.reader() as conn_rst:
                search_rst = SearchService(conn_rst, settings=settings_rst)
                res_rst = search_rst.search("offline search")

            assert len(res_rst.results) == len(res_src.results)
            assert res_rst.results[0].title == res_src.results[0].title
            assert res_rst.results[0].url == res_src.results[0].url
            assert res_rst.total_approx == res_src.total_approx

            home_rst = HomeService(settings=settings_rst)
            with db_restored.reader() as conn_rst:
                payload_rst = home_rst.get_home(conn_rst, now=now)
            assert len(payload_rst.latest) == len(payload_src.latest)
            assert payload_rst.latest[0].title == payload_src.latest[0].title
        finally:
            db_restored.close()

    finally:
        db.close()


def test_backup_during_concurrent_write(tmp_path: Path) -> None:
    """Proves SQLite online backup API captures consistent state during WAL writes."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    source_dir = tmp_path / "source_data"
    db_path = source_dir / "index" / "search.db"
    raw_dir = source_dir / "raw"
    image_dir = source_dir / "images"

    db = Database(db_path).open()
    try:
        _populate_sample_store(db, raw_dir, image_dir, now)

        # Open a concurrent writer connection in WAL mode
        writer = connect(db_path, read_only=False)
        try:
            # Perform a write in an explicit transaction
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                """
                INSERT INTO news_items (
                    id, source_id, link, title, summary, published_at, category,
                    status, first_seen_at, updated_at
                )
                VALUES (
                    'news-concurrent', 1, 'https://offline.example/concurrent',
                    'Concurrent Story', 'Active write', ?, 'General', 'active', ?, ?
                )
                """,
                (now.isoformat(), now.isoformat(), now.isoformat()),
            )

            # While writer transaction is active, execute online backup
            backup_dir = tmp_path / "backup_concurrent"
            manifest = create_backup(
                db_path=db_path,
                output_dir=backup_dir,
                raw_dir=raw_dir,
                image_dir=image_dir,
            )
            assert manifest.manifest_version == 1

            # Commit the writer transaction
            writer.execute("COMMIT")
        finally:
            writer.close()

        # Restore the backup snapshot and verify it is non-corrupt
        target_dir = tmp_path / "restored_concurrent"
        res = restore_backup(backup_dir, target_dir)
        assert res.integrity_ok is True
        assert res.consistency.is_consistent is True

    finally:
        db.close()


def test_restore_refuses_non_empty_target(tmp_path: Path) -> None:
    """Restore must strictly refuse non-empty directories."""
    db_path = tmp_path / "orig.db"
    db = Database(db_path).open()
    db.close()

    backup_dir = tmp_path / "backup_test"
    create_backup(db_path, backup_dir)

    # Create target directory containing an existing file
    target_dir = tmp_path / "dirty_target"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "preexisting.txt").write_text("blocked", encoding="utf-8")

    with pytest.raises(StorageError) as exc_info:
        restore_backup(backup_dir, target_dir)
    assert exc_info.value.code == "restore_target_not_empty"


def test_restore_rejects_corrupted_manifest(tmp_path: Path) -> None:
    """Missing or corrupted manifest.json is rejected."""
    empty_backup = tmp_path / "empty_backup"
    empty_backup.mkdir()
    with pytest.raises(StorageError) as exc:
        restore_backup(empty_backup, tmp_path / "target1")
    assert exc.value.code == "backup_manifest_missing"

    bad_json_backup = tmp_path / "bad_json_backup"
    bad_json_backup.mkdir()
    (bad_json_backup / "manifest.json").write_text("{not-valid-json", encoding="utf-8")
    with pytest.raises(StorageError) as exc2:
        restore_backup(bad_json_backup, tmp_path / "target2")
    assert exc2.value.code == "backup_manifest_corrupted"

    incompatible_backup = tmp_path / "incompatible_backup"
    incompatible_backup.mkdir()
    bad_manifest = {
        "manifest_version": 999,
        "engine_version": "0.1.0",
        "created_at": "2026-11-14T00:00:00Z",
        "schema_version": 4,
        "index_version": 1,
        "database": {"path": "database.db", "sha256": "abc", "size_bytes": 10},
        "files": [],
    }
    (incompatible_backup / "manifest.json").write_text(json.dumps(bad_manifest), encoding="utf-8")
    with pytest.raises(StorageError) as exc3:
        restore_backup(incompatible_backup, tmp_path / "target3")
    assert exc3.value.code == "backup_incompatible"


def test_restore_rejects_corrupted_database(tmp_path: Path) -> None:
    """Truncated or hash-mismatched database file is rejected."""
    db_path = tmp_path / "sample.db"
    db = Database(db_path).open()
    db.close()

    backup_dir = tmp_path / "backup_corrupt"
    create_backup(db_path, backup_dir)

    # Modify database.db so sha256 doesn't match
    db_file = backup_dir / "database.db"
    db_file.write_bytes(b"corrupted contents")

    with pytest.raises(StorageError) as exc:
        restore_backup(backup_dir, tmp_path / "target")
    assert exc.value.code == "backup_corrupted"


def test_restore_rejects_missing_file(tmp_path: Path) -> None:
    """Missing associated file recorded in manifest triggers rejection."""
    db_path = tmp_path / "test.db"
    raw_dir = tmp_path / "raw"
    store = FileStore(tmp_path)
    store.put_raw(b"some content")
    db = Database(db_path).open()
    db.close()

    backup_dir = tmp_path / "backup_files"
    create_backup(db_path, backup_dir, raw_dir=raw_dir)

    # Remove the raw file from backup directory
    for f in (backup_dir / "raw").rglob("*.gz"):
        f.unlink()

    with pytest.raises(StorageError) as exc:
        restore_backup(backup_dir, tmp_path / "target")
    assert exc.value.code == "backup_corrupted"


def test_cli_backup_and_restore(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI backup and restore commands produce valid outputs."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    db_path = tmp_path / "cli_src" / "index" / "search.db"
    raw_dir = tmp_path / "cli_src" / "raw"
    image_dir = tmp_path / "cli_src" / "images"

    db = Database(db_path).open()
    try:
        _populate_sample_store(db, raw_dir, image_dir, now)
    finally:
        db.close()

    backup_dir = tmp_path / "cli_backup"
    rc = main(
        [
            "backup",
            "--db",
            str(db_path),
            "--raw-dir",
            str(raw_dir),
            "--image-dir",
            str(image_dir),
            "--output",
            str(backup_dir),
            "--json",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    manifest_data = json.loads(out)
    assert manifest_data["manifest_version"] == 1
    assert (backup_dir / "manifest.json").is_file()

    restored_dir = tmp_path / "cli_restored"
    rc_rest = main(
        [
            "restore",
            "--backup-dir",
            str(backup_dir),
            "--target-dir",
            str(restored_dir),
            "--json",
        ]
    )
    assert rc_rest == 0
    out_rest = capsys.readouterr().out
    rest_data = json.loads(out_rest)
    assert rest_data["integrity_ok"] is True
    assert (restored_dir / "index" / "search.db").is_file()


def test_offline_mode_search_and_home_with_sockets_blocked(tmp_path: Path) -> None:
    """Proves search and home services function completely with sockets blocked (offline mode)."""
    # Note: no @pytest.mark.loopback marker is on this test; pytest-socket blocks all sockets
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    db_path = tmp_path / "offline_db.db"
    raw_dir = tmp_path / "raw"
    image_dir = tmp_path / "images"

    db = Database(db_path).open()
    try:
        _populate_sample_store(db, raw_dir, image_dir, now)

        settings = load_settings(
            overrides={
                "db_path": str(db_path),
                "offline_mode": "true",
            }
        )
        assert settings.offline_mode is True

        # Perform search offline
        with db.reader() as conn:
            search_svc = SearchService(conn, settings=settings)
            res = search_svc.search("offline retrieval")
            assert len(res.results) > 0
            assert "Independent" in res.results[0].title

        # Build home offline
        home_svc = HomeService(settings=settings)
        with db.reader() as conn:
            home_data = home_svc.get_home(conn, now=now)
        assert len(home_data.latest) == 1
    finally:
        db.close()


def test_offline_mode_config_validation() -> None:
    """offline_mode and real_network_enabled cannot both be True."""
    s = Settings(offline_mode=True)
    assert s.offline_mode is True

    with pytest.raises(
        ValueError, match="offline_mode and real_network_enabled cannot both be true"
    ):
        Settings(offline_mode=True, real_network_enabled=True)


def test_restore_rejects_corrupted_database_integrity_check(tmp_path: Path) -> None:
    """Corrupted database bytes with matching manifest hash are rejected by integrity check."""
    db_path = tmp_path / "valid.db"
    db = Database(db_path).open()
    db.close()

    backup_dir = tmp_path / "backup_integrity"
    create_backup(db_path, backup_dir)

    # Overwrite database with garbage text but update manifest.json hash to match
    db_file = backup_dir / "database.db"
    db_file.write_bytes(b"SQLite format 3\x00corrupted garbage data blocks\x00" * 20)
    new_hash = compute_sha256(db_file)

    manifest_file = backup_dir / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_data["database"]["sha256"] = new_hash
    manifest_data["database"]["size_bytes"] = db_file.stat().st_size
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(StorageError) as exc:
        restore_backup(backup_dir, tmp_path / "target_integrity")
    assert exc.value.code in {"database_corrupt", "storage_error"}


def test_cli_backup_and_restore_human_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test CLI commands without --json flag output human-readable summaries."""
    db_path = tmp_path / "human.db"
    db = Database(db_path).open()
    db.close()

    backup_dir = tmp_path / "human_backup"
    rc_bk = main(["backup", "--db", str(db_path), "--output", str(backup_dir)])
    assert rc_bk == 0
    out_bk = capsys.readouterr().out
    assert "Backup created successfully at:" in out_bk
    assert "Schema version:" in out_bk

    target_dir = tmp_path / "human_target"
    rc_rst = main(["restore", "-b", str(backup_dir), "-t", str(target_dir)])
    assert rc_rst == 0
    out_rst = capsys.readouterr().out
    assert "Restore completed successfully into:" in out_rst
    assert "Integrity:      PASSED" in out_rst


def test_cli_error_handling(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI commands return exit code 1 on missing DB or non-empty target."""
    missing_db = tmp_path / "nonexistent.db"
    rc1 = main(["backup", "--db", str(missing_db)])
    assert rc1 == 1

    rc2 = main(["restore", "-b", str(tmp_path / "nonexistent_backup"), "-t", str(tmp_path / "t")])
    assert rc2 == 1
