"""Operator controls for blocking, removal, takedown, pausing, and status inspection.

Implements architectural decision D11 & D12 operator requirements:
- block/unblock (hosts, registered domains, URL prefixes) applied to frontier and index.
- remove (--url, --domain) with FTS unindexing and optional raw file purge.
- news source disabling and item removal.
- image takedown with thumbnail file deletion.
- entity removal.
- crawler pause, resume, and stop controls via signal/flag files.
- drive safety and permission audits.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from search_engine.config import Settings
from search_engine.crawler.normalize import normalize
from search_engine.db import Database
from search_engine.errors import StorageError
from search_engine.storage import FileStore

logger = logging.getLogger(__name__)

_CLOUD_SYNC_NAMES = {
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "iclouddrive",
    "icloud",
    "nextcloud",
    "box sync",
    "box",
}


@dataclass(frozen=True, slots=True)
class BlockRecord:
    id: int
    kind: str
    target: str
    reason: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class RemovalReport:
    target: str
    kind: str
    urls_blocked: int
    documents_removed: int
    fts_rows_deleted: int
    raw_files_purged: int
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "kind": self.kind,
            "urls_blocked": self.urls_blocked,
            "documents_removed": self.documents_removed,
            "fts_rows_deleted": self.fts_rows_deleted,
            "raw_files_purged": self.raw_files_purged,
            "details": list(self.details),
        }


@dataclass(frozen=True, slots=True)
class EngineStatusReport:
    status: str
    paused: bool
    stopped: bool
    free_disk_bytes: int
    active_documents: int
    active_news_items: int
    active_images: int
    active_blocks: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "paused": self.paused,
            "stopped": self.stopped,
            "free_disk_bytes": self.free_disk_bytes,
            "active_documents": self.active_documents,
            "active_news_items": self.active_news_items,
            "active_images": self.active_images,
            "active_blocks": self.active_blocks,
            "warnings": list(self.warnings),
        }


def inspect_drive_and_permissions(data_dir: Path) -> list[str]:
    """Inspect data directory drive and permissions; return human-readable warnings."""
    warnings: list[str] = []
    p_str = str(data_dir.resolve())

    # 1. Check for UNC / Network shares
    if p_str.startswith(r"\\") or p_str.startswith("//"):
        warnings.append(
            f"Data directory '{p_str}' is on a network share (UNC path). "
            "SQLite WAL mode requires local disk to prevent lock corruption."
        )

    # 2. Check for Cloud Sync folders (OneDrive, Dropbox, etc.)
    parts_lower = [part.lower() for part in data_dir.resolve().parts]
    for sync_name in _CLOUD_SYNC_NAMES:
        if any(sync_name in p for p in parts_lower):
            warnings.append(
                f"Data directory '{p_str}' appears to be inside a cloud-synchronized folder "
                f"({sync_name}). Cloud sync engines can lock or corrupt SQLite WAL files."
            )
            break

    # 3. Check for World-Writable permissions on POSIX
    if hasattr(os, "stat") and os.name != "nt":
        try:
            mode = data_dir.stat().st_mode
            if mode & 0o002:
                warnings.append(
                    f"Data directory '{p_str}' is world-writable (mode {oct(mode)}). "
                    "Restrict permissions to the current operator."
                )
        except OSError:
            pass

    return warnings


def block_target(
    db: Database,
    target: str,
    kind: str,
    *,
    reason: str = "operator_block",
) -> BlockRecord:
    """Add a host, domain, or URL prefix to the operator blocklist and deindex matching content."""
    clean_kind = kind.strip().lower()
    if clean_kind not in {"host", "domain", "prefix"}:
        raise StorageError(f"invalid block kind '{kind}', must be host, domain, or prefix")

    clean_target = target.strip()
    if clean_kind in {"host", "domain"}:
        clean_target = clean_target.lower().rstrip(".")
    elif clean_kind == "prefix":
        try:
            clean_target = normalize(clean_target).key
        except Exception:
            clean_target = clean_target.lower()

    now_iso = datetime.now(UTC).isoformat()

    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO operator_blocks (kind, target, reason, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (target) DO UPDATE SET
                kind = excluded.kind,
                reason = excluded.reason,
                created_at = excluded.created_at
            """,
            (clean_kind, clean_target, reason, now_iso),
        )
        row = conn.execute(
            "SELECT id, kind, target, reason, created_at FROM operator_blocks WHERE target = ?",
            (clean_target,),
        ).fetchone()

        # Update URLs table to blocked
        if clean_kind == "host":
            conn.execute(
                """
                UPDATE urls SET status = 'blocked', last_reason = ?
                WHERE host_id IN (SELECT id FROM hosts WHERE host = ?)
                """,
                (reason, clean_target),
            )
            doc_rows = conn.execute(
                """
                SELECT id FROM documents
                WHERE url_id IN (
                    SELECT id FROM urls WHERE host_id IN (SELECT id FROM hosts WHERE host = ?)
                )
                """,
                (clean_target,),
            ).fetchall()
        elif clean_kind == "domain":
            conn.execute(
                """
                UPDATE urls SET status = 'blocked', last_reason = ?
                WHERE host_id IN (SELECT id FROM hosts WHERE registered_domain = ?)
                """,
                (reason, clean_target),
            )
            doc_rows = conn.execute(
                """
                SELECT id FROM documents
                WHERE url_id IN (
                    SELECT id FROM urls
                    WHERE host_id IN (SELECT id FROM hosts WHERE registered_domain = ?)
                )
                """,
                (clean_target,),
            ).fetchall()
        else:  # prefix
            pattern = clean_target + "%"
            conn.execute(
                "UPDATE urls SET status = 'blocked', last_reason = ? WHERE url_key LIKE ?",
                (reason, pattern),
            )
            doc_rows = conn.execute(
                """
                SELECT id FROM documents
                WHERE url_id IN (SELECT id FROM urls WHERE url_key LIKE ?)
                   OR final_url LIKE ?
                   OR canonical_url LIKE ?
                """,
                (pattern, pattern, pattern),
            ).fetchall()

        # Mark matching documents removed and delete from FTS
        for r in doc_rows:
            d_id = int(r[0])
            conn.execute(
                "UPDATE documents SET status = 'removed', updated_at = ? WHERE id = ?",
                (now_iso, d_id),
            )
            conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d_id,))
            conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (d_id,))

        record = BlockRecord(
            id=int(row[0]),
            kind=str(row[1]),
            target=str(row[2]),
            reason=str(row[3]),
            created_at=str(row[4]),
        )
    return record


def unblock_target(db: Database, target: str, kind: str) -> bool:
    """Remove a target from the operator blocklist."""
    clean_target = target.strip()
    if kind in {"host", "domain"}:
        clean_target = clean_target.lower().rstrip(".")
    elif kind == "prefix":
        try:
            clean_target = normalize(clean_target).key
        except Exception:
            clean_target = clean_target.lower()

    with db.transaction() as conn:
        cur = conn.execute(
            "DELETE FROM operator_blocks WHERE target = ? AND kind = ?", (clean_target, kind)
        )
        return cur.rowcount > 0


def list_blocks(db: Database) -> list[BlockRecord]:
    """Retrieve all active operator blocks."""
    with db.reader() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, target, reason, created_at
            FROM operator_blocks
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [
        BlockRecord(
            id=int(r[0]),
            kind=str(r[1]),
            target=str(r[2]),
            reason=str(r[3]),
            created_at=str(r[4]),
        )
        for r in rows
    ]


def remove_document(
    db: Database,
    store: FileStore | None = None,
    *,
    url: str | None = None,
    domain: str | None = None,
    purge_raw: bool = False,
    reason: str = "operator_removal",
) -> RemovalReport:
    """Remove documents by exact URL or registered domain.

    Deindexes documents from FTS and optionally purges unreferenced raw files.
    """
    if not url and not domain:
        raise StorageError("either url or domain must be provided for document removal")

    now_iso = datetime.now(UTC).isoformat()
    urls_blocked = 0
    docs_removed = 0
    fts_deleted = 0
    raw_purged = 0
    details: list[str] = []

    with db.transaction() as conn:
        target_doc_ids: list[int] = []
        raw_hashes_to_check: set[str] = set()

        if url:
            target_str = url
            kind_str = "url"
            try:
                norm = normalize(url)
                norm_key = norm.key
            except Exception:
                norm_key = url

            # Find matching document
            cur = conn.execute(
                """
                SELECT id, raw_hash FROM documents
                WHERE final_url = ? OR canonical_url = ?
                   OR url_id IN (SELECT id FROM urls WHERE url_key = ?)
                """,
                (url, url, norm_key),
            )
            for r in cur.fetchall():
                target_doc_ids.append(int(r[0]))
                if r[1]:
                    raw_hashes_to_check.add(str(r[1]))

            # Update URL status
            u_cur = conn.execute(
                "UPDATE urls SET status = 'blocked', last_reason = ? WHERE url_key = ?",
                (reason, norm_key),
            )
            urls_blocked += u_cur.rowcount

        else:
            if domain is None:
                raise StorageError("Domain must be specified")
            target_str = domain
            kind_str = "domain"
            clean_dom = domain.strip().lower().rstrip(".")

            # Find all matching documents
            cur = conn.execute(
                """
                SELECT d.id, d.raw_hash FROM documents d
                JOIN urls u ON d.url_id = u.id
                JOIN hosts h ON u.host_id = h.id
                WHERE h.registered_domain = ? OR h.host = ?
                """,
                (clean_dom, clean_dom),
            )
            for r in cur.fetchall():
                target_doc_ids.append(int(r[0]))
                if r[1]:
                    raw_hashes_to_check.add(str(r[1]))

            # Update URLs
            u_cur = conn.execute(
                """
                UPDATE urls SET status = 'blocked', last_reason = ?
                WHERE host_id IN (SELECT id FROM hosts WHERE registered_domain = ? OR host = ?)
                """,
                (reason, clean_dom, clean_dom),
            )
            urls_blocked += u_cur.rowcount

        # Mark documents removed and delete from FTS
        for d_id in target_doc_ids:
            cur_up = conn.execute(
                "UPDATE documents SET status = 'removed', updated_at = ? WHERE id = ?",
                (now_iso, d_id),
            )
            docs_removed += cur_up.rowcount
            cur_fts1 = conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d_id,))
            cur_fts2 = conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (d_id,))
            fts_deleted += cur_fts1.rowcount + cur_fts2.rowcount
            details.append(f"document {d_id} marked removed")

        # Record removal event
        conn.execute(
            """
            INSERT INTO events (run_id, at, reason, subject, detail)
            VALUES ('operator', ?, 'operator_removed', ?, ?)
            """,
            (now_iso, target_str, reason),
        )

        # Optional raw file purge
        if purge_raw and store is not None:
            for raw_h in raw_hashes_to_check:
                # Check if any OTHER active document still uses this hash
                in_use = conn.execute(
                    "SELECT 1 FROM documents WHERE raw_hash = ? AND status = 'active' LIMIT 1",
                    (raw_h,),
                ).fetchone()
                if not in_use:
                    try:
                        p = store.raw_path(raw_h)
                        if p.exists():
                            p.unlink()
                            raw_purged += 1
                            details.append(f"purged raw file: {p.name}")
                    except Exception as exc:
                        details.append(f"raw file purge failed for {raw_h}: {exc}")

    return RemovalReport(
        target=target_str,
        kind=kind_str,
        urls_blocked=urls_blocked,
        documents_removed=docs_removed,
        fts_rows_deleted=fts_deleted,
        raw_files_purged=raw_purged,
        details=details,
    )


def disable_news_source(db: Database, source_id_or_url: str) -> bool:
    """Disable a news source so its items disappear from home and search."""
    now_iso = datetime.now(UTC).isoformat()
    clean_val = source_id_or_url.strip()

    with db.transaction() as conn:
        if clean_val.isdigit():
            cur = conn.execute(
                "UPDATE news_sources SET enabled = 0 WHERE id = ?", (int(clean_val),)
            )
            src_ids = [int(clean_val)]
        else:
            cur = conn.execute(
                "UPDATE news_sources SET enabled = 0 WHERE feed_url_key = ? OR name = ?",
                (clean_val, clean_val),
            )
            rows = conn.execute(
                "SELECT id FROM news_sources WHERE feed_url_key = ? OR name = ?",
                (clean_val, clean_val),
            ).fetchall()
            src_ids = [int(r[0]) for r in rows]

        if cur.rowcount == 0:
            return False

        # Deactivate news items
        for sid in src_ids:
            conn.execute(
                "UPDATE news_items SET status = 'inactive', updated_at = ? WHERE source_id = ?",
                (now_iso, sid),
            )

            # Remove matching news documents from FTS
            doc_rows = conn.execute(
                """
                SELECT d.id FROM documents d
                JOIN news_items n ON d.news_item_id = n.id
                WHERE n.source_id = ?
                """,
                (sid,),
            ).fetchall()

            for r in doc_rows:
                d_id = int(r[0])
                conn.execute(
                    "UPDATE documents SET status = 'inactive', updated_at = ? WHERE id = ?",
                    (now_iso, d_id),
                )
                conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d_id,))
                conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (d_id,))

        return True


def remove_news_item(db: Database, item_id: str, *, reason: str = "operator_removal") -> bool:
    """Remove a specific news item from home and search."""
    now_iso = datetime.now(UTC).isoformat()
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE news_items SET status = 'inactive', updated_at = ? WHERE id = ?",
            (now_iso, item_id),
        )
        if cur.rowcount == 0:
            return False

        doc = conn.execute("SELECT id FROM documents WHERE news_item_id = ?", (item_id,)).fetchone()
        if doc:
            d_id = int(doc[0])
            conn.execute(
                "UPDATE documents SET status = 'removed', updated_at = ? WHERE id = ?",
                (now_iso, d_id),
            )
            conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d_id,))
            conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (d_id,))

        conn.execute(
            """
            INSERT INTO events (run_id, at, reason, subject, detail)
            VALUES ('operator', ?, 'news_removed', ?, ?)
            """,
            (now_iso, item_id, reason),
        )
        return True


def takedown_image(
    db: Database,
    store: FileStore | None,
    image_id: str,
    *,
    reason: str = "operator_takedown",
) -> bool:
    """Takedown an image: mark removed in database and delete WebP files from disk."""
    now_iso = datetime.now(UTC).isoformat()
    with db.transaction() as conn:
        row = conn.execute("SELECT content_hash FROM images WHERE id = ?", (image_id,)).fetchone()
        if not row:
            return False

        content_hash = str(row[0])
        conn.execute("UPDATE images SET status = 'removed' WHERE id = ?", (image_id,))
        conn.execute(
            """
            INSERT INTO events (run_id, at, reason, subject, detail)
            VALUES ('operator', ?, 'image_takedown', ?, ?)
            """,
            (now_iso, image_id, reason),
        )

        if store is not None:
            for size in (160, 480):
                try:
                    p = store.image_path(content_hash, size)
                    if p.exists():
                        p.unlink()
                except OSError as exc:
                    logger.debug("Failed to delete thumbnail %s: %s", p, exc)

        return True


def remove_entity(db: Database, entity_id: str | int) -> bool:
    """Remove an entity from the knowledge store."""
    with db.transaction() as conn:
        if isinstance(entity_id, int) or entity_id.isdigit():
            cur = conn.execute("DELETE FROM entities WHERE id = ?", (int(entity_id),))
        else:
            cur = conn.execute("DELETE FROM entities WHERE name = ?", (str(entity_id),))
        return cur.rowcount > 0


def pause_crawler(data_dir: Path) -> Path:
    """Signal the crawler to pause by creating a PAUSE file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    pause_file = data_dir / "PAUSE"
    pause_file.write_text(f"PAUSED at {datetime.now(UTC).isoformat()}\n", encoding="utf-8")
    return pause_file


def resume_crawler(data_dir: Path) -> bool:
    """Signal the crawler to resume by removing the PAUSE file."""
    pause_file = data_dir / "PAUSE"
    if pause_file.exists():
        pause_file.unlink()
        return True
    return False


def stop_crawler(data_dir: Path) -> Path:
    """Signal the crawler to gracefully stop by creating a STOP file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    stop_file = data_dir / "STOP"
    stop_file.write_text(f"STOP at {datetime.now(UTC).isoformat()}\n", encoding="utf-8")
    return stop_file


def check_engine_status(settings: Settings, db: Database) -> EngineStatusReport:
    """Compile comprehensive operational health, disk, and lock status."""
    data_dir = Path(settings.db_path).parent.parent
    stop_file = data_dir / "STOP"
    pause_file = data_dir / "PAUSE"

    warnings = inspect_drive_and_permissions(data_dir)

    # Disk space
    try:
        free_bytes = shutil.disk_usage(data_dir).free
    except OSError:
        free_bytes = 0

    if free_bytes < settings.min_free_disk_bytes:
        warnings.append(
            f"Low disk space: {free_bytes / (1024 * 1024):.1f} MB free, "
            f"minimum required is {settings.min_free_disk_bytes / (1024 * 1024):.1f} MB."
        )

    # Database records
    with db.reader() as conn:
        active_docs = int(
            conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'active'").fetchone()[0]
        )
        active_news = int(
            conn.execute("SELECT COUNT(*) FROM news_items WHERE status = 'active'").fetchone()[0]
        )
        active_images = int(
            conn.execute("SELECT COUNT(*) FROM images WHERE status = 'active'").fetchone()[0]
        )
        try:
            active_blocks = int(conn.execute("SELECT COUNT(*) FROM operator_blocks").fetchone()[0])
        except Exception:
            active_blocks = 0

    is_paused = pause_file.exists()
    is_stopped = stop_file.exists()

    if is_stopped:
        overall_status = "stopped"
    elif is_paused:
        overall_status = "paused"
    elif warnings:
        overall_status = "warning"
    else:
        overall_status = "healthy"

    return EngineStatusReport(
        status=overall_status,
        paused=is_paused,
        stopped=is_stopped,
        free_disk_bytes=free_bytes,
        active_documents=active_docs,
        active_news_items=active_news,
        active_images=active_images,
        active_blocks=active_blocks,
        warnings=warnings,
    )
