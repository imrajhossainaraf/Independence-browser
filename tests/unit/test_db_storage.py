from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

from search_engine import storage
from search_engine.db import (
    MIGRATIONS_DIR,
    Database,
    Migration,
    connect,
    discover_migrations,
    migrate,
)
from search_engine.errors import StorageError
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository
from search_engine.storage import FileStore, atomic_write, recover, sha256_hex


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "data" / "engine.db").open()


def test_initializes_from_empty_directory(tmp_path: Path) -> None:
    with Database(tmp_path / "new" / "dir" / "e.db") as database:
        assert database.schema_version == len(discover_migrations())
        with database.reader() as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
    assert {"hosts", "urls", "documents", "news_items", "images", "entities"} <= tables


def test_wal_mode_on_disk(db: Database) -> None:
    with db.reader() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_restart_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    with Database(path) as first, first.transaction() as conn:
        HostRepository(conn).ensure("example.com", "example.com")
    with Database(path) as second, second.reader() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0] == 1


def test_upgrade_each_migration(tmp_path: Path) -> None:
    migrations = discover_migrations()
    conn = connect(tmp_path / "u.db")
    for count in range(1, len(migrations) + 1):
        migrate(conn, migrations[:count], backup_dir=tmp_path / "bk")
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == count
    assert migrate(conn, migrations) == []
    conn.close()


def test_backup_made_before_upgrade(tmp_path: Path) -> None:
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "0001_a.sql").write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")
    conn = connect(tmp_path / "b.db")
    migrate(conn, discover_migrations(mdir), backup_dir=tmp_path / "bk")
    (mdir / "0002_b.sql").write_text("CREATE TABLE b (x INTEGER);", encoding="utf-8")
    migrate(conn, discover_migrations(mdir), backup_dir=tmp_path / "bk")
    assert len(list((tmp_path / "bk").glob("pre-migrate-v1-*.db"))) == 1
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "0001_bad.sql").write_text(
        "CREATE TABLE a (x INTEGER);\nCREATE TABLE a (x INTEGER);", encoding="utf-8"
    )
    conn = connect(tmp_path / "f.db")
    with pytest.raises(StorageError) as info:
        migrate(conn, discover_migrations(mdir))
    assert info.value.code == "migration_failed"
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='a'").fetchone()[0] == 0
    conn.close()


def test_schema_too_new_refused(tmp_path: Path) -> None:
    conn = connect(tmp_path / "n.db")
    migrate(conn, discover_migrations())
    conn.execute("INSERT INTO schema_version VALUES (99, 'future', 'x')")
    with pytest.raises(StorageError) as info:
        migrate(conn, discover_migrations())
    assert info.value.code == "schema_too_new"
    conn.close()


def test_migration_numbering_validated(tmp_path: Path) -> None:
    (tmp_path / "0002_gap.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(StorageError):
        discover_migrations(tmp_path)
    (tmp_path / "0002_gap.sql").rename(tmp_path / "bad name.sql")
    with pytest.raises(StorageError):
        discover_migrations(tmp_path)


def test_migration_dataclass_and_dir_exist() -> None:
    assert MIGRATIONS_DIR.is_dir()
    assert isinstance(discover_migrations()[0], Migration)


def test_uniqueness_and_idempotent_inserts(db: Database) -> None:
    with db.transaction() as conn:
        host_id = HostRepository(conn).ensure("example.com", "example.com")
        assert HostRepository(conn).ensure("example.com", "example.com") == host_id
        urls = UrlRepository(conn)
        first, created = urls.add("https://example.com/", host_id, source="seed")
        again, created_again = urls.add("https://example.com/", host_id, source="seed")
    assert created and not created_again and first == again
    with db.reader() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM urls")  # query_only


def test_check_constraints_enforced(db: Database) -> None:
    def bad_insert() -> None:
        with db.transaction() as conn:
            host_id = HostRepository(conn).ensure("example.com", "example.com")
            conn.execute(
                "INSERT INTO urls (url_key, host_id, source, status, discovered_at, updated_at) "
                "VALUES ('k', ?, 'seed', 'bogus', 'x', 'x')",
                (host_id,),
            )

    with pytest.raises(StorageError):
        bad_insert()


def test_document_requires_exactly_one_owner(db: Database) -> None:
    with pytest.raises(StorageError), db.transaction() as conn:
        conn.execute("INSERT INTO documents (fetched_at) VALUES ('x')")


def test_transaction_rolls_back_on_error(db: Database) -> None:
    def failing() -> None:
        with db.transaction() as conn:
            HostRepository(conn).ensure("example.com", "example.com")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing()
    with db.reader() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0] == 0


def test_closed_database_refuses_writes(tmp_path: Path) -> None:
    database = Database(tmp_path / "c.db")
    with pytest.raises(StorageError), database.transaction():
        pass


def test_reader_works_during_write(db: Database) -> None:
    with db.transaction() as conn:
        HostRepository(conn).ensure("example.com", "example.com")
        seen: list[int] = []

        def read() -> None:
            with db.reader() as r:
                seen.append(r.execute("SELECT COUNT(*) FROM hosts").fetchone()[0])

        thread = threading.Thread(target=read)
        thread.start()
        thread.join(5)
    assert seen == [0]


def test_document_upsert_and_status(db: Database) -> None:
    with db.transaction() as conn:
        host_id = HostRepository(conn).ensure("example.com", "example.com")
        urls = UrlRepository(conn)
        url_id, _ = urls.add("https://example.com/a", host_id, source="seed")
        docs = DocumentRepository(conn)
        first = docs.upsert_page(url_id, title="A", raw_hash=None)
        second = docs.upsert_page(url_id, title="B", raw_hash=None)
        urls.set_status(url_id, "done", reason="stored")
        assert docs.count() == 1
        assert urls.status("https://example.com/a") == "done"
        assert urls.status("https://example.com/none") is None
    assert first == second


# ---- file storage ----


def test_paths_are_hash_only_and_short(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    digest = sha256_hex(b"https://evil.example/../../x?q=1")
    raw = store.raw_path(digest)
    image = store.image_path(digest, 640)
    assert raw.name == f"{digest}.gz" and raw.parent.name == digest[2:4]
    assert image.name == f"{digest}-640.webp"
    for path in (raw, image):
        assert "evil" not in str(path)
        assert len(path.name) + 20 < storage.MAX_PATH_LENGTH
        assert len(str(path.relative_to(tmp_path))) < 100


@pytest.mark.parametrize("bad", ["../etc/passwd", "ab", "G" * 64, "A" * 64, ""])
def test_bad_hashes_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(StorageError):
        FileStore(tmp_path).raw_path(bad)


def test_bad_image_size_rejected(tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        FileStore(tmp_path).image_path("a" * 64, 0)


def test_raw_roundtrip_and_idempotent(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    digest = store.put_raw(b"<html>hello</html>")
    assert store.put_raw(b"<html>hello</html>") == digest
    assert store.get_raw(digest) == b"<html>hello</html>"
    with pytest.raises(StorageError):
        store.get_raw("b" * 64)


def test_corrupt_raw_file_reported(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    digest = store.put_raw(b"data")
    store.raw_path(digest).write_bytes(b"not gzip")
    with pytest.raises(StorageError):
        store.get_raw(digest)


def test_interrupted_write_leaves_no_partial_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "x" / "file.bin"

    def boom(src: object, dst: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(StorageError):
        atomic_write(target, b"data")
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_replace_retries_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real = os.replace

    def flaky(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("locked")
        real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    monkeypatch.setattr(storage, "_REPLACE_DELAY_S", 0)
    atomic_write(tmp_path / "f.bin", b"ok")
    assert (tmp_path / "f.bin").read_bytes() == b"ok" and calls["n"] == 3


def test_permission_error_eventually_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always(src: object, dst: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", always)
    monkeypatch.setattr(storage, "_REPLACE_DELAY_S", 0)
    with pytest.raises(StorageError):
        atomic_write(tmp_path / "f.bin", b"ok")


def test_recovery_finds_orphans_missing_and_temp(db: Database, tmp_path: Path) -> None:
    store = FileStore(tmp_path / "files")
    kept = store.put_raw(b"kept")
    orphan = store.put_raw(b"orphan")
    missing = "c" * 64
    store.put_image("d" * 64, 100, b"img")
    stale = store.raw_path(kept).parent / ".tmp-abc.part"
    stale.write_bytes(b"partial")
    with db.transaction() as conn:
        host_id = HostRepository(conn).ensure("example.com", "example.com")
        urls = UrlRepository(conn)
        for index, digest in enumerate((kept, missing)):
            url_id, _ = urls.add(f"https://example.com/{index}", host_id, source="seed")
            DocumentRepository(conn).upsert_page(url_id, title="t", raw_hash=digest)
        conn.execute(
            "INSERT INTO images (id, source_url, content_hash, fetched_at) "
            "VALUES ('i1', 'https://example.com/i', ?, 'x')",
            ("e" * 64,),
        )
    with db.reader() as conn:
        report = recover(conn, store)
    assert [p.name for p in report.orphan_raw] == [f"{orphan}.gz"]
    assert report.missing_raw == [missing]
    assert [p.name for p in report.orphan_images] == [f"{'d' * 64}-100.webp"]
    assert report.missing_images == ["e" * 64]
    assert report.temp_removed == 1 and not stale.exists()
    assert not report.consistent


def test_recovery_consistent_when_empty(db: Database, tmp_path: Path) -> None:
    with db.reader() as conn:
        assert recover(conn, FileStore(tmp_path / "none")).consistent
