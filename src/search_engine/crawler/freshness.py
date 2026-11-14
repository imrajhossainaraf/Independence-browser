"""Recrawl policy, freshness evaluation, and conditional requests (Step 17, decision D6, D8).

Implements:
- Stale document determination based on recrawl_after_days threshold.
- Re-enqueuing of stale URLs from 'done' back to 'pending' with reset retry count.
- Extraction of ETag and Last-Modified headers for conditional requests.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_stale(
    fetched_at_iso: str,
    *,
    recrawl_after_days: int = 7,
    now: datetime | None = None,
) -> bool:
    """Return True if fetched_at_iso timestamp is older than recrawl_after_days."""
    if not fetched_at_iso:
        return True
    try:
        dt = datetime.fromisoformat(fetched_at_iso)
        current = now or _utc_now()
        return (current - dt) >= timedelta(days=recrawl_after_days)
    except (ValueError, TypeError):
        return True


def enqueue_stale_urls(
    conn: sqlite3.Connection,
    *,
    recrawl_after_days: int = 7,
    limit: int = 500,
    now: datetime | None = None,
) -> int:
    """Find active documents older than recrawl_after_days and re-enqueue for recrawl.

    Returns the number of URLs re-enqueued for recrawl.
    """
    current = now or _utc_now()
    cutoff = (current - timedelta(days=recrawl_after_days)).isoformat()
    now_iso = current.isoformat()

    cur = conn.execute(
        """
        SELECT u.id
        FROM urls u
        JOIN documents d ON u.id = d.url_id
        WHERE u.status = 'done'
          AND d.status = 'active'
          AND d.fetched_at <= ?
        LIMIT ?
        """,
        (cutoff, limit),
    )
    stale_ids = [int(r[0]) for r in cur.fetchall()]
    if not stale_ids:
        return 0

    placeholders = ", ".join(["?"] * len(stale_ids))
    conn.execute(
        f"""
        UPDATE urls
        SET status = 'pending',
            retry_count = 0,
            updated_at = ?
        WHERE id IN ({placeholders})
        """,  # noqa: S608
        (now_iso, *stale_ids),
    )
    return len(stale_ids)


def get_document_conditional_headers(
    conn: sqlite3.Connection, url_id: int
) -> tuple[str | None, str | None]:
    """Retrieve (etag, last_modified_header) for an existing document row, if any."""
    row = conn.execute(
        "SELECT etag, last_modified_header FROM documents WHERE url_id = ?",
        (url_id,),
    ).fetchone()
    if row is None:
        return None, None
    etag = str(row[0]) if row[0] else None
    last_mod = str(row[1]) if row[1] else None
    return etag, last_mod
