"""News source models, health evaluation, summary cleaning, and retention per D16."""

from __future__ import annotations

import html
import logging
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dateutil import parser as date_parser
from selectolax.lexbor import LexborHTMLParser

from search_engine.indexer.build import upsert_document_index

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsSourceConfig:
    """Configuration for an approved news source."""

    feed_url: str
    name: str
    category: str = "General"
    poll_interval_minutes: int = 30
    images_allowed: bool = False


def clean_summary(raw: str, max_chars: int = 300) -> str:
    """Clean summary text: strip HTML, decompose scripts/styles, decode entities, limit length."""
    if not raw or not raw.strip():
        return ""
    parser = LexborHTMLParser(raw)
    for tag in parser.css("script, style"):
        tag.decompose()
    text = parser.text(separator=" ", strip=True) if parser.body else raw
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def parse_news_sources(lines: Sequence[str]) -> list[NewsSourceConfig]:
    """Parse news_sources.txt lines per D16.

    Format: feed URL, display name, category, poll interval, images allowed yes or no.
    Lines starting with # and blank lines are ignored.
    """
    sources: list[NewsSourceConfig] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) < 2:
            continue
        feed_url = parts[0]
        name = parts[1]
        category = parts[2] if len(parts) > 2 and parts[2] else "General"
        poll_interval = 30
        if len(parts) > 3 and parts[3]:
            try:
                poll_interval = max(15, int(parts[3]))
            except ValueError:
                poll_interval = 30
        images_allowed = False
        if len(parts) > 4 and parts[4]:
            val = parts[4].lower()
            images_allowed = val in ("yes", "true", "1", "y")
        sources.append(
            NewsSourceConfig(
                feed_url=feed_url,
                name=name,
                category=category,
                poll_interval_minutes=poll_interval,
                images_allowed=images_allowed,
            )
        )
    return sources


def load_news_sources_file(path: Path) -> list[NewsSourceConfig]:
    """Load and parse news sources from a file."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return parse_news_sources(lines)


@dataclass
class NewsSourceHealth:
    """Health information for a news source."""

    id: int
    name: str
    feed_url_key: str
    category: str
    poll_interval_minutes: int
    last_success_at: str | None
    last_error_at: str | None
    last_error: str | None
    consecutive_failures: int
    is_healthy: bool
    is_stale: bool


def get_sources_health(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> list[NewsSourceHealth]:
    """Evaluate health and stale status for all configured news sources."""
    if now is None:
        now = datetime.now(UTC)
    rows = conn.execute(
        """
        SELECT id, name, feed_url_key, category, poll_interval_minutes,
               last_success_at, last_error_at, last_error, consecutive_failures
        FROM news_sources
        ORDER BY id ASC
        """
    ).fetchall()

    health_list: list[NewsSourceHealth] = []
    for r in rows:
        failures = int(r["consecutive_failures"])
        last_success = r["last_success_at"]
        interval = int(r["poll_interval_minutes"])
        is_healthy = (failures == 0) and (last_success is not None)
        is_stale = False
        if last_success is None:
            is_stale = True
        else:
            try:
                dt_success = date_parser.parse(str(last_success))
                if dt_success.tzinfo is None:
                    dt_success = dt_success.replace(tzinfo=UTC)
                age_minutes = (now - dt_success.astimezone(UTC)).total_seconds() / 60.0
                if age_minutes > 2 * interval:
                    is_stale = True
            except (ValueError, OverflowError):
                is_stale = True
        if failures > 0:
            is_stale = True

        health_list.append(
            NewsSourceHealth(
                id=int(r["id"]),
                name=str(r["name"]),
                feed_url_key=str(r["feed_url_key"]),
                category=str(r["category"]),
                poll_interval_minutes=interval,
                last_success_at=str(last_success) if last_success is not None else None,
                last_error_at=str(r["last_error_at"]) if r["last_error_at"] is not None else None,
                last_error=str(r["last_error"]) if r["last_error"] is not None else None,
                consecutive_failures=failures,
                is_healthy=is_healthy,
                is_stale=is_stale,
            )
        )
    return health_list


def _deactivate_news_item(conn: sqlite3.Connection, item_id: str) -> None:
    """Deactivate a news item and update its document and FTS status."""
    conn.execute("UPDATE news_items SET status = 'inactive' WHERE id = ?", (item_id,))
    row = conn.execute("SELECT id FROM documents WHERE news_item_id = ?", (item_id,)).fetchone()
    if row is not None:
        doc_id = int(row[0])
        conn.execute("UPDATE documents SET status = 'inactive' WHERE id = ?", (doc_id,))
        upsert_document_index(conn, doc_id)


def apply_news_retention(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    max_days: int = 14,
    max_active: int = 500,
) -> tuple[int, int]:
    """Deactivate items older than max_days and cap total active items to max_active per D16.

    Returns (expired_count, cap_pruned_count).
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff_iso = (now - timedelta(days=max_days)).isoformat()

    # 1. Expire items older than max_days
    rows = conn.execute(
        "SELECT id FROM news_items WHERE status = 'active' AND published_at < ?",
        (cutoff_iso,),
    ).fetchall()
    expired_ids = [str(r["id"]) for r in rows]
    for item_id in expired_ids:
        _deactivate_news_item(conn, item_id)

    # 2. Hard cap: at most max_active active items, oldest pruned first
    active_rows = conn.execute(
        "SELECT id FROM news_items WHERE status = 'active' ORDER BY published_at ASC"
    ).fetchall()
    cap_pruned_count = 0
    if len(active_rows) > max_active:
        excess = len(active_rows) - max_active
        for r in active_rows[:excess]:
            _deactivate_news_item(conn, str(r["id"]))
            cap_pruned_count += 1

    return len(expired_ids), cap_pruned_count
