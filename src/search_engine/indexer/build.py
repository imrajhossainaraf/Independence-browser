"""Full rebuild, single-document upsert, removal, and build reporting for FTS5 index."""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from search_engine.indexer.fts import (
    INDEX_VERSION,
    MAX_TRI_PREFIX_CHARS,
    decompress_text,
    tokenize_url,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class IndexBuildReport:
    run_id: str
    started_at: str
    finished_at: str = ""
    duration_seconds: float = 0.0
    total_documents_examined: int = 0
    indexed_count: int = 0
    skipped_count: int = 0
    removed_count: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    index_version: int = INDEX_VERSION

    def finish(self) -> None:
        self.finished_at = _utc_now_iso()
        try:
            t0 = datetime.fromisoformat(self.started_at)
            t1 = datetime.fromisoformat(self.finished_at)
            self.duration_seconds = max(0.0, (t1 - t0).total_seconds())
        except Exception:
            self.duration_seconds = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "total_documents_examined": self.total_documents_examined,
            "indexed_count": self.indexed_count,
            "skipped_count": self.skipped_count,
            "removed_count": self.removed_count,
            "by_type": dict(self.by_type),
            "index_version": self.index_version,
        }

    def render(self) -> str:
        lines = [
            f"Index Build Report [{self.run_id}]",
            f"Index version: {self.index_version}",
            f"Duration:      {self.duration_seconds:.2f} s",
            f"Examined:      {self.total_documents_examined}",
            f"Indexed:       {self.indexed_count}",
            f"Skipped:       {self.skipped_count}",
        ]
        if self.by_type:
            lines.append("By document type:")
            for doc_type, count in sorted(self.by_type.items()):
                lines.append(f"  {doc_type.ljust(10)} {count}")
        return "\n".join(lines)


def get_index_version(conn: sqlite3.Connection) -> int:
    """Read the current index version from index_state."""
    row = conn.execute("SELECT value FROM index_state WHERE key = 'index_version'").fetchone()
    if row is None or not row[0]:
        return 0
    try:
        return int(row[0])
    except ValueError:
        return 0


def set_index_version(conn: sqlite3.Connection, version: int) -> None:
    """Store the index version in index_state."""
    conn.execute(
        "INSERT INTO index_state (key, value) VALUES ('index_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def rebuild_index(conn: sqlite3.Connection, *, run_id: str | None = None) -> IndexBuildReport:
    """Rebuild doc_fts and doc_fts_tri from scratch for all active documents.

    Transaction-safe: operates within the caller's transaction or commits atomically.
    """
    t0 = time.perf_counter()
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    report = IndexBuildReport(
        run_id=run_id or f"rebuild-{uuid.uuid4().hex[:8]}",
        started_at=_utc_now_iso(),
        index_version=INDEX_VERSION,
    )

    # 1. Clear existing FTS index tables
    conn.execute("DELETE FROM doc_fts")
    conn.execute("DELETE FROM doc_fts_tri")

    # 2. Query all documents and join optional news summary
    query = """
        SELECT
            d.id,
            d.doc_type,
            d.final_url,
            d.canonical_url,
            d.lang,
            d.status,
            d.title,
            d.description,
            d.headings,
            d.text_gz,
            ni.summary AS news_summary
        FROM documents d
        LEFT JOIN news_items ni ON d.news_item_id = ni.id
        ORDER BY d.id ASC
    """
    rows = conn.execute(query).fetchall()
    report.total_documents_examined = len(rows)

    active_doc_ids: list[int] = []

    for r in rows:
        doc_id = int(r["id"])
        status = str(r["status"])
        doc_type = str(r["doc_type"])

        # Check eligibility: active and indexable doc_type
        if status != "active" or doc_type not in ("page", "pdf", "text", "news"):
            report.skipped_count += 1
            continue

        title = str(r["title"] or "")
        headings = str(r["headings"] or "")
        description = str(r["description"] or "")

        # For news documents, body is news summary or description per D16
        if doc_type == "news":
            body = str(r["news_summary"] or description)
            headings = ""
        else:
            body = decompress_text(r["text_gz"])

        url = str(r["canonical_url"] or r["final_url"] or "")
        url_tokens = tokenize_url(url)
        body_prefix = body[:MAX_TRI_PREFIX_CHARS]

        # Insert into primary FTS table
        conn.execute(
            """
            INSERT INTO doc_fts (rowid, title, headings, description, body, url_tokens)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (doc_id, title, headings, description, body, url_tokens),
        )

        # Insert into trigram table
        conn.execute(
            """
            INSERT INTO doc_fts_tri (rowid, title, body_prefix)
            VALUES (?, ?, ?)
            """,
            (doc_id, title, body_prefix),
        )

        active_doc_ids.append(doc_id)
        report.indexed_count += 1
        report.by_type[doc_type] = report.by_type.get(doc_type, 0) + 1

    # 3. Update indexed_version on documents
    if active_doc_ids:
        # Batch update indexed_version for all active documents
        conn.execute("UPDATE documents SET indexed_version = 0")
        conn.executemany(
            "UPDATE documents SET indexed_version = ? WHERE id = ?",
            [(INDEX_VERSION, did) for did in active_doc_ids],
        )
    else:
        conn.execute("UPDATE documents SET indexed_version = 0")

    # 4. Update index_state metadata
    set_index_version(conn, INDEX_VERSION)
    conn.execute(
        "INSERT INTO index_state (key, value) VALUES ('last_rebuild_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_utc_now_iso(),),
    )

    elapsed = time.perf_counter() - t0
    report.finish()
    report.duration_seconds = max(report.duration_seconds, elapsed)

    conn.execute(
        """
        INSERT INTO metrics (at, name, value, labels)
        VALUES (?, 'index_rebuild_duration_seconds', ?, ?)
        """,
        (_utc_now_iso(), elapsed, report.run_id),
    )
    conn.execute(
        """
        INSERT INTO metrics (at, name, value, labels)
        VALUES (?, 'index_documents_indexed', ?, ?)
        """,
        (_utc_now_iso(), float(report.indexed_count), report.run_id),
    )

    return report


def upsert_document_index(conn: sqlite3.Connection, doc_id: int) -> bool:
    """Upsert one document into doc_fts and doc_fts_tri if active, or remove if not."""
    query = """
        SELECT
            d.id,
            d.doc_type,
            d.final_url,
            d.canonical_url,
            d.lang,
            d.status,
            d.title,
            d.description,
            d.headings,
            d.text_gz,
            ni.summary AS news_summary
        FROM documents d
        LEFT JOIN news_items ni ON d.news_item_id = ni.id
        WHERE d.id = ?
    """
    row = conn.execute(query, (doc_id,)).fetchone()
    if row is None:
        remove_document_index(conn, doc_id)
        return False

    status = str(row["status"])
    doc_type = str(row["doc_type"])

    # If inactive, removed, noindex, metadata_only, or unsupported: remove from FTS
    if status != "active" or doc_type not in ("page", "pdf", "text", "news"):
        remove_document_index(conn, doc_id)
        return False

    title = str(row["title"] or "")
    headings = str(row["headings"] or "")
    description = str(row["description"] or "")

    if doc_type == "news":
        body = str(row["news_summary"] or description)
        headings = ""
    else:
        body = decompress_text(row["text_gz"])

    url = str(row["canonical_url"] or row["final_url"] or "")
    url_tokens = tokenize_url(url)
    body_prefix = body[:MAX_TRI_PREFIX_CHARS]

    # Insert or replace into doc_fts
    conn.execute(
        """
        INSERT OR REPLACE INTO doc_fts (rowid, title, headings, description, body, url_tokens)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (doc_id, title, headings, description, body, url_tokens),
    )

    # Insert or replace into doc_fts_tri
    conn.execute(
        """
        INSERT OR REPLACE INTO doc_fts_tri (rowid, title, body_prefix)
        VALUES (?, ?, ?)
        """,
        (doc_id, title, body_prefix),
    )

    conn.execute(
        "UPDATE documents SET indexed_version = ? WHERE id = ?",
        (INDEX_VERSION, doc_id),
    )
    return True


def remove_document_index(conn: sqlite3.Connection, doc_id: int) -> bool:
    """Remove a document from doc_fts and doc_fts_tri."""
    cur1 = conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (doc_id,))
    cur2 = conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (doc_id,))
    conn.execute("UPDATE documents SET indexed_version = 0 WHERE id = ?", (doc_id,))
    return cur1.rowcount > 0 or cur2.rowcount > 0


def deactivate_document(
    conn: sqlite3.Connection, doc_id: int, *, reason: str = "deactivated"
) -> bool:
    """Mark a document as inactive and remove it from the FTS index."""
    now = _utc_now_iso()
    cur = conn.execute(
        "UPDATE documents SET status = 'inactive', updated_at = ? WHERE id = ?",
        (now, doc_id),
    )
    remove_document_index(conn, doc_id)
    return cur.rowcount > 0
