"""Hash-addressed, atomic file storage for raw pages and images, plus recovery.

Paths come only from a SHA-256 hash (never from URL text), so they are short and safe.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from search_engine.errors import StorageError

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REPLACE_ATTEMPTS = 8
_REPLACE_DELAY_S = 0.05
MAX_PATH_LENGTH = 260


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_hash(digest: str) -> str:
    if _HASH.match(digest) is None:
        raise StorageError("invalid content hash", code="storage_error")
    return digest


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, fsync, then ``os.replace``.

    Retries briefly on Windows ``PermissionError`` (antivirus or indexer holding the target).
    Directory fsync is skipped on purpose (unsupported on Windows).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY_S)
    except OSError as exc:
        raise StorageError(f"file write failed: {exc}", code="storage_error") from exc
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class FileStore:
    """Root of the data directory: ``raw/ab/cd/<sha>.gz`` and ``images/ab/cd/<sha>-<size>.webp``."""

    root: Path

    def _shard(self, kind: str, digest: str) -> Path:
        return self.root / kind / digest[:2] / digest[2:4]

    def raw_path(self, digest: str) -> Path:
        _check_hash(digest)
        return self._shard("raw", digest) / f"{digest}.gz"

    def image_path(self, digest: str, size: int) -> Path:
        _check_hash(digest)
        if size <= 0:
            raise StorageError("invalid image size", code="storage_error")
        return self._shard("images", digest) / f"{digest}-{size}.webp"

    def put_raw(self, body: bytes) -> str:
        """Store a gzip of ``body`` addressed by the hash of the raw bytes; idempotent."""
        digest = sha256_hex(body)
        path = self.raw_path(digest)
        if not path.exists():
            atomic_write(path, gzip.compress(body, mtime=0))
        return digest

    def get_raw(self, digest: str) -> bytes:
        path = self.raw_path(digest)
        try:
            return gzip.decompress(path.read_bytes())
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise StorageError(f"raw file unreadable: {digest}", code="storage_error") from exc

    def put_image(self, digest: str, size: int, data: bytes) -> Path:
        path = self.image_path(digest, size)
        if not path.exists():
            atomic_write(path, data)
        return path

    def _files(self, kind: str, suffix: str) -> dict[str, Path]:
        found: dict[str, Path] = {}
        base = self.root / kind
        if base.exists():
            for path in base.rglob(f"*{suffix}"):
                if not path.name.startswith(".tmp-"):
                    found[path.name[:64]] = path
        return found

    def stale_temp_files(self) -> list[Path]:
        return sorted(self.root.rglob(".tmp-*.part")) if self.root.exists() else []


@dataclass(slots=True)
class RecoveryReport:
    orphan_raw: list[Path] = field(default_factory=list)
    orphan_images: list[Path] = field(default_factory=list)
    missing_raw: list[str] = field(default_factory=list)
    missing_images: list[str] = field(default_factory=list)
    temp_removed: int = 0

    @property
    def consistent(self) -> bool:
        return not (
            self.orphan_raw or self.orphan_images or self.missing_raw or self.missing_images
        )


def recover(
    conn: sqlite3.Connection, store: FileStore, *, remove_temp: bool = True
) -> RecoveryReport:
    """Compare files and rows. Removes only interrupted temp files; reports the rest."""
    report = RecoveryReport()
    if remove_temp:
        for tmp in store.stale_temp_files():
            tmp.unlink(missing_ok=True)
            report.temp_removed += 1
    raw_files = store._files("raw", ".gz")  # noqa: SLF001
    image_files = store._files("images", ".webp")  # noqa: SLF001
    raw_rows = {
        str(r[0])
        for r in conn.execute("SELECT DISTINCT raw_hash FROM documents WHERE raw_hash IS NOT NULL")
    }
    image_rows = {str(r[0]) for r in conn.execute("SELECT content_hash FROM images")}
    report.orphan_raw = sorted(p for d, p in raw_files.items() if d not in raw_rows)
    report.orphan_images = sorted(p for d, p in image_files.items() if d not in image_rows)
    report.missing_raw = sorted(raw_rows - raw_files.keys())
    report.missing_images = sorted(image_rows - image_files.keys())
    return report
