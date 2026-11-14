"""Small repositories over a writer connection. Plain parameterized SQL only."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


class HostRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def ensure(self, host: str, registered_domain: str, *, now: str | None = None) -> int:
        self._conn.execute(
            "INSERT OR IGNORE INTO hosts (host, registered_domain, created_at) VALUES (?, ?, ?)",
            (host, registered_domain, now or _now()),
        )
        row = self._conn.execute("SELECT id FROM hosts WHERE host = ?", (host,)).fetchone()
        return int(row[0])


class UrlRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add(
        self,
        url_key: str,
        host_id: int,
        *,
        source: str,
        depth: int = 0,
        priority: int = 0,
        parent_url_id: int | None = None,
        now: str | None = None,
    ) -> tuple[int, bool]:
        """Insert a URL once. Returns ``(id, created)``."""
        stamp = now or _now()
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO urls "
            "(url_key, host_id, source, depth, priority, parent_url_id, discovered_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (url_key, host_id, source, depth, priority, parent_url_id, stamp, stamp),
        )
        created = cursor.rowcount == 1
        row = self._conn.execute("SELECT id FROM urls WHERE url_key = ?", (url_key,)).fetchone()
        return int(row[0]), created

    def status(self, url_key: str) -> str | None:
        row = self._conn.execute("SELECT status FROM urls WHERE url_key = ?", (url_key,)).fetchone()
        return None if row is None else str(row[0])

    def set_status(self, url_id: int, status: str, *, reason: str | None = None) -> None:
        self._conn.execute(
            "UPDATE urls SET status = ?, last_reason = ?, updated_at = ? WHERE id = ?",
            (status, reason, _now(), url_id),
        )


class DocumentRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_page(
        self,
        url_id: int,
        *,
        title: str,
        raw_hash: str | None,
        content_hash: str | None = None,
        status: str = "active",
        fetched_at: str | None = None,
    ) -> int:
        stamp = fetched_at or _now()
        self._conn.execute(
            "INSERT INTO documents "
            "(url_id, title, raw_hash, content_hash, status, fetched_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (url_id) DO UPDATE SET title = excluded.title, "
            "raw_hash = excluded.raw_hash, content_hash = excluded.content_hash, "
            "status = excluded.status, fetched_at = excluded.fetched_at, "
            "updated_at = excluded.updated_at",
            (url_id, title, raw_hash, content_hash, status, stamp, stamp),
        )
        row = self._conn.execute("SELECT id FROM documents WHERE url_id = ?", (url_id,)).fetchone()
        return int(row[0])

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def upsert_document(
        self,
        *,
        url_id: int | None = None,
        news_item_id: str | None = None,
        doc_type: str = "page",
        final_url: str | None = None,
        canonical_url: str | None = None,
        title: str = "",
        headings: str = "",
        description: str = "",
        text_gz: bytes | None = None,
        lang: str = "und",
        status: str = "active",
        word_count: int = 0,
        content_hash: str | None = None,
        raw_hash: str | None = None,
        published_at: str | None = None,
        fetched_at: str | None = None,
    ) -> int:
        if (url_id is None and news_item_id is None) or (
            url_id is not None and news_item_id is not None
        ):
            raise ValueError("Exactly one of url_id or news_item_id must be provided")

        stamp = fetched_at or _now()
        cols = (
            "doc_type, final_url, canonical_url, title, headings, description, "
            "text_gz, lang, status, word_count, content_hash, raw_hash, "
            "published_at, fetched_at, updated_at"
        )
        update_clause = (
            "doc_type = excluded.doc_type, final_url = excluded.final_url, "
            "canonical_url = excluded.canonical_url, title = excluded.title, "
            "headings = excluded.headings, description = excluded.description, "
            "text_gz = excluded.text_gz, lang = excluded.lang, status = excluded.status, "
            "word_count = excluded.word_count, content_hash = excluded.content_hash, "
            "raw_hash = excluded.raw_hash, published_at = excluded.published_at, "
            "fetched_at = excluded.fetched_at, updated_at = excluded.updated_at"
        )
        values = (
            doc_type,
            final_url,
            canonical_url,
            title,
            headings,
            description,
            text_gz,
            lang,
            status,
            word_count,
            content_hash,
            raw_hash,
            published_at,
            stamp,
            stamp,
        )

        if url_id is not None:
            sql = (
                f"INSERT INTO documents (url_id, {cols}) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                f"ON CONFLICT (url_id) DO UPDATE SET {update_clause}"
            )
            self._conn.execute(sql, (url_id, *values))
            row = self._conn.execute(
                "SELECT id FROM documents WHERE url_id = ?", (url_id,)
            ).fetchone()
            return int(row[0])
        else:
            sql = (
                f"INSERT INTO documents (news_item_id, {cols}) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                f"ON CONFLICT (news_item_id) DO UPDATE SET {update_clause}"
            )
            self._conn.execute(sql, (news_item_id, *values))
            row = self._conn.execute(
                "SELECT id FROM documents WHERE news_item_id = ?", (news_item_id,)
            ).fetchone()
            return int(row[0])
