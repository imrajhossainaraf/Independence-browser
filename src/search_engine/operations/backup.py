"""Online backup, manifest hashing, integrity verification, and safe restoration.

Implements architectural decision D11:
- Backup using the SQLite online backup API (Connection.backup), never a live file copy.
- Content-addressed raw files and images included with SHA-256 hashes in manifest.json.
- Restore into a new empty directory only.
- Strict verification: SHA-256 hashes, PRAGMA integrity_check, and index consistency.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from search_engine import __version__
from search_engine.db import backup_database, connect, current_version
from search_engine.errors import StorageError
from search_engine.indexer.consistency import ConsistencyReport, verify_index_consistency
from search_engine.indexer.fts import INDEX_VERSION

CHUNK_SIZE = 64 * 1024


def compute_sha256(path: Path) -> str:
    """Calculate SHA-256 hex digest of a file in chunks."""
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                h.update(chunk)
    except OSError as exc:
        raise StorageError(
            f"failed reading file for hash calculation: {exc}", code="storage_error"
        ) from exc
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class BackupFileEntry:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class BackupManifest:
    manifest_version: int
    engine_version: str
    created_at: str
    schema_version: int
    index_version: int
    database: BackupFileEntry
    files: list[BackupFileEntry] = field(default_factory=list)
    total_files: int = 1
    total_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "engine_version": self.engine_version,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "index_version": self.index_version,
            "database": asdict(self.database),
            "files": [asdict(f) for f in self.files],
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupManifest:
        try:
            db_data = data["database"]
            db_entry = BackupFileEntry(
                path=str(db_data["path"]),
                sha256=str(db_data["sha256"]),
                size_bytes=int(db_data["size_bytes"]),
            )
            files = [
                BackupFileEntry(
                    path=str(f["path"]),
                    sha256=str(f["sha256"]),
                    size_bytes=int(f["size_bytes"]),
                )
                for f in data.get("files", [])
            ]
            return cls(
                manifest_version=int(data["manifest_version"]),
                engine_version=str(data["engine_version"]),
                created_at=str(data["created_at"]),
                schema_version=int(data["schema_version"]),
                index_version=int(data["index_version"]),
                database=db_entry,
                files=files,
                total_files=int(data.get("total_files", len(files) + 1)),
                total_bytes=int(
                    data.get("total_bytes", sum(f.size_bytes for f in files) + db_entry.size_bytes)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StorageError(
                f"invalid backup manifest format: {exc}", code="backup_manifest_corrupted"
            ) from exc


@dataclass(frozen=True, slots=True)
class RestoreResult:
    target_dir: Path
    database_path: Path
    files_restored: int
    bytes_restored: int
    schema_version: int
    index_version: int
    integrity_ok: bool
    consistency: ConsistencyReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_dir": str(self.target_dir),
            "database_path": str(self.database_path),
            "files_restored": self.files_restored,
            "bytes_restored": self.bytes_restored,
            "schema_version": self.schema_version,
            "index_version": self.index_version,
            "integrity_ok": self.integrity_ok,
            "consistency": self.consistency.to_dict(),
        }


def create_backup(
    db_path: Path,
    output_dir: Path,
    *,
    raw_dir: Path | None = None,
    image_dir: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> BackupManifest:
    """Create a consistent online snapshot of SQLite database, raw files, and images.

    Uses ``sqlite3.Connection.backup`` to safely snapshot WAL-mode SQLite databases
    even while concurrent writers or readers are active.
    """
    if output_dir.exists() and any(output_dir.iterdir()):
        raise StorageError(
            f"output directory already exists and is not empty: {output_dir}",
            code="backup_target_not_empty",
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    target_db_path = output_dir / "database.db"

    # 1. Snapshot database using SQLite online backup API
    close_conn = False
    source_conn = conn
    if source_conn is None:
        source_conn = connect(db_path, read_only=False)
        close_conn = True

    try:
        backup_database(source_conn, target_db_path)
        schema_ver = current_version(source_conn)
    finally:
        if close_conn and source_conn is not None:
            source_conn.close()

    db_sha256 = compute_sha256(target_db_path)
    db_size = target_db_path.stat().st_size
    db_entry = BackupFileEntry(path="database.db", sha256=db_sha256, size_bytes=db_size)

    file_entries: list[BackupFileEntry] = []

    # 2. Snapshot raw storage files (skip .tmp-* partial files)
    if raw_dir is not None and raw_dir.exists():
        raw_target_base = output_dir / "raw"
        for p in sorted(raw_dir.rglob("*.gz")):
            if p.is_file() and not p.name.startswith(".tmp-"):
                rel = p.relative_to(raw_dir)
                dst = raw_target_base / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                file_sha = compute_sha256(dst)
                file_size = dst.stat().st_size
                file_entries.append(
                    BackupFileEntry(
                        path=f"raw/{rel.as_posix()}", sha256=file_sha, size_bytes=file_size
                    )
                )

    # 3. Snapshot image storage files
    if image_dir is not None and image_dir.exists():
        image_target_base = output_dir / "images"
        for p in sorted(image_dir.rglob("*.webp")):
            if p.is_file() and not p.name.startswith(".tmp-"):
                rel = p.relative_to(image_dir)
                dst = image_target_base / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                file_sha = compute_sha256(dst)
                file_size = dst.stat().st_size
                file_entries.append(
                    BackupFileEntry(
                        path=f"images/{rel.as_posix()}", sha256=file_sha, size_bytes=file_size
                    )
                )

    total_files = len(file_entries) + 1
    total_bytes = sum(f.size_bytes for f in file_entries) + db_size

    manifest = BackupManifest(
        manifest_version=1,
        engine_version=__version__,
        created_at=datetime.now(UTC).isoformat(),
        schema_version=schema_ver,
        index_version=INDEX_VERSION,
        database=db_entry,
        files=file_entries,
        total_files=total_files,
        total_bytes=total_bytes,
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")

    return manifest


def verify_backup(backup_dir: Path) -> BackupManifest:
    """Validate a backup directory: manifest exists, all files present, and hashes match."""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise StorageError("manifest.json missing from backup", code="backup_manifest_missing")

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise StorageError(
            f"manifest.json is not valid JSON: {exc}", code="backup_manifest_corrupted"
        ) from exc

    manifest = BackupManifest.from_dict(data)
    if manifest.manifest_version != 1:
        raise StorageError(
            f"unsupported manifest version {manifest.manifest_version}", code="backup_incompatible"
        )

    # Verify database file
    db_file = backup_dir / manifest.database.path
    if not db_file.is_file():
        raise StorageError(
            f"backup database file missing: {manifest.database.path}", code="backup_corrupted"
        )
    actual_db_hash = compute_sha256(db_file)
    if actual_db_hash != manifest.database.sha256:
        raise StorageError(
            f"database hash mismatch: expected {manifest.database.sha256}, got {actual_db_hash}",
            code="backup_corrupted",
        )

    # Verify each associated file
    for entry in manifest.files:
        f_path = backup_dir / entry.path
        if not f_path.is_file():
            raise StorageError(f"backup file missing: {entry.path}", code="backup_corrupted")
        actual_hash = compute_sha256(f_path)
        if actual_hash != entry.sha256:
            raise StorageError(
                f"file hash mismatch for {entry.path}: expected {entry.sha256}, got {actual_hash}",
                code="backup_corrupted",
            )

    return manifest


def restore_backup(
    backup_dir: Path,
    target_dir: Path,
    *,
    target_db_rel: str = "index/search.db",
) -> RestoreResult:
    """Restore a verified backup into a NEW, EMPTY target directory only.

    Verifies:
    1. Target directory is strictly empty.
    2. Manifest exists and matches SHA-256 digests of all backup files.
    3. Restored database passes SQLite PRAGMA integrity_check.
    4. Restored index passes verify_index_consistency().
    """
    if target_dir.exists():
        if any(target_dir.iterdir()):
            raise StorageError(
                f"target directory must be empty, but contains files: {target_dir}",
                code="restore_target_not_empty",
            )
    else:
        target_dir.mkdir(parents=True, exist_ok=True)

    # Verify backup integrity first
    manifest = verify_backup(backup_dir)

    # Restore database
    target_db = target_dir / target_db_rel
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_dir / manifest.database.path, target_db)

    # Restore files
    for entry in manifest.files:
        src = backup_dir / entry.path
        dst = target_dir / entry.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # Verify database integrity and index consistency
    try:
        conn = sqlite3.connect(target_db)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            cursor = conn.execute("PRAGMA integrity_check")
            rows = cursor.fetchall()
            if rows != [("ok",)]:
                raise StorageError(
                    f"database integrity check failed on restored database: {rows}",
                    code="database_corrupt",
                )

            report = verify_index_consistency(conn)
            if not report.is_consistent:
                raise StorageError(
                    f"index consistency check failed on restored database: {report.errors}",
                    code="index_inconsistent",
                )
            restored_schema_version = current_version(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise StorageError(
            f"database integrity check failed on restored database: {exc}",
            code="database_corrupt",
        ) from exc

    return RestoreResult(
        target_dir=target_dir,
        database_path=target_db,
        files_restored=manifest.total_files,
        bytes_restored=manifest.total_bytes,
        schema_version=restored_schema_version,
        index_version=manifest.index_version,
        integrity_ok=True,
        consistency=report,
    )
