"""SQLite access: connections, forward-only migrations, and the single-writer Database.

Plain ``sqlite3`` with parameterized SQL only. WAL, foreign keys, busy timeout.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from search_engine.errors import StorageError

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_BUSY_TIMEOUT_MS = 5000
_MIGRATION_NAME = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql\Z")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def translate_error(exc: sqlite3.Error) -> StorageError:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return StorageError("database is busy", code="storage_busy")
    return StorageError(f"database error: {exc}", code="storage_error")


def connect(
    path: Path, *, busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS, read_only: bool = False
) -> sqlite3.Connection:
    """Open a connection with explicit transactions, WAL, foreign keys and a busy timeout."""
    try:
        conn = sqlite3.connect(
            path, isolation_level=None, timeout=busy_timeout_ms / 1000, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        conn.execute("PRAGMA foreign_keys = ON")
        if read_only:
            conn.execute("PRAGMA query_only = ON")
        else:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error as exc:
        raise translate_error(exc) from exc
    return conn


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Numbered files ``0001_name.sql`` must start at 1 and have no gaps or duplicates."""
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.match(path.name)
        if match is None:
            raise StorageError(f"bad migration file name: {path.name}", code="migration_invalid")
        found.append(Migration(int(match.group(1)), match.group(2), path))
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise StorageError(
                f"migration numbering is not contiguous at {migration.path.name}",
                code="migration_invalid",
            )
    return found


def _statements(sql: str) -> Iterator[str]:
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        yield buffer.strip()


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()
    return int(row[0])


def backup_database(conn: sqlite3.Connection, destination: Path) -> None:
    """Online backup through the SQLite backup API (never a live file copy)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(destination)
    try:
        conn.backup(target)
    finally:
        target.close()


def migrate(
    conn: sqlite3.Connection,
    migrations: list[Migration],
    *,
    backup_dir: Path | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> list[int]:
    """Apply pending migrations, each in its own transaction. Returns the versions applied."""
    version = current_version(conn)
    latest = migrations[-1].version if migrations else 0
    if version > latest:
        raise StorageError(
            f"database schema v{version} is newer than this program (v{latest})",
            code="schema_too_new",
        )
    pending = [m for m in migrations if m.version > version]
    if not pending:
        return []
    if version > 0 and backup_dir is not None:
        stamp = clock().strftime("%Y%m%dT%H%M%SZ")
        backup_database(conn, backup_dir / f"pre-migrate-v{version}-{stamp}.db")
    applied: list[int] = []
    for migration in pending:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _statements(migration.path.read_text(encoding="utf-8")):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, clock().isoformat()),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise StorageError(
                f"migration {migration.path.name} failed: {exc}", code="migration_failed"
            ) from exc
        applied.append(migration.version)
    return applied


class Database:
    """One database file: migrated on open, one writer at a time, many readers (WAL)."""

    def __init__(
        self,
        path: Path,
        *,
        migrations_dir: Path = MIGRATIONS_DIR,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = path
        self._migrations_dir = migrations_dir
        self._busy_timeout_ms = busy_timeout_ms
        self._clock = clock
        self._lock = threading.Lock()
        self._writer: sqlite3.Connection | None = None

    def open(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(self.path, busy_timeout_ms=self._busy_timeout_ms)
        try:
            migrate(
                conn,
                discover_migrations(self._migrations_dir),
                backup_dir=self.path.parent / "backups",
                clock=self._clock,
            )
        except BaseException:
            conn.close()
            raise
        self._writer = conn
        return self

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def schema_version(self) -> int:
        with self.reader() as conn:
            return current_version(conn)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """The single writer: ``BEGIN IMMEDIATE`` ... commit, or roll back on any error."""
        if self._writer is None:
            raise StorageError("database is not open", code="storage_error")
        with self._lock:
            conn = self._writer
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise translate_error(exc) from exc
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    raise translate_error(exc) from exc
                raise

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        """A fresh read-only connection; never blocked by the writer in WAL mode."""
        conn = connect(self.path, busy_timeout_ms=self._busy_timeout_ms, read_only=True)
        try:
            yield conn
        finally:
            conn.close()
