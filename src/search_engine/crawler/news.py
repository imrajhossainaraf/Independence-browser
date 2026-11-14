"""News feed crawler and poller per D16."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from search_engine.config import Settings
from search_engine.crawler.fetcher import Redirect, SafeFetcher
from search_engine.crawler.normalize import normalize
from search_engine.crawler.robots import RobotsCache
from search_engine.crawler.scheduler import Denial, Scheduler
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.db import Database
from search_engine.experience.clusters import cluster_news_items
from search_engine.experience.news import (
    NewsSourceConfig,
    apply_news_retention,
    clean_summary,
    load_news_sources_file,
)
from search_engine.extract.feed import FeedAdapter
from search_engine.extract.models import AdapterStatus
from search_engine.indexer.build import upsert_document_index
from search_engine.indexer.consistency import verify_index_consistency
from search_engine.lock import LockHeldError, ProcessLock
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.resolver import Resolver, SystemResolver
from search_engine.net.transport import Transport
from search_engine.repositories import DocumentRepository

logger = logging.getLogger(__name__)


def sync_news_sources(
    conn: sqlite3.Connection,
    sources: Sequence[NewsSourceConfig],
    *,
    allow_loopback: bool = False,
) -> int:
    """Sync news sources into the database."""
    synced = 0
    for s in sources:
        try:
            norm = normalize(s.feed_url, allow_loopback=allow_loopback)
        except (UrlRejected, ValueError):
            continue
        conn.execute(
            """
            INSERT INTO news_sources (
                feed_url_key, name, category, poll_interval_minutes, images_allowed, enabled
            )
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT (feed_url_key) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                poll_interval_minutes = excluded.poll_interval_minutes,
                images_allowed = excluded.images_allowed
            """,
            (norm.key, s.name, s.category, s.poll_interval_minutes, 1 if s.images_allowed else 0),
        )
        synced += 1
    return synced


@dataclass
class NewsPollReport:
    """Structured report produced by a news poll run."""

    run_id: str
    status: str  # "success" | "refused_lock_held" | "error"
    sources_polled: int = 0
    sources_success: int = 0
    sources_failed: int = 0
    sources_not_modified: int = 0
    items_added: int = 0
    items_updated: int = 0
    items_expired: int = 0
    items_cap_pruned: int = 0
    clusters_count: int = 0
    errors: list[str] = field(default_factory=list)
    feed_outcomes: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            f"News Poll Report [run_id: {self.run_id}]",
            f"Status: {self.status}",
            (
                f"Sources: {self.sources_polled} polled "
                f"({self.sources_success} ok, {self.sources_not_modified} 304, "
                f"{self.sources_failed} failed)"
            ),
            (
                f"Items: {self.items_added} added, {self.items_updated} updated, "
                f"{self.items_expired} expired, {self.items_cap_pruned} cap-pruned"
            ),
            f"Clusters: {self.clusters_count} active clusters",
        ]
        if self.feed_outcomes:
            lines.append("Feed Outcomes:")
            for feed, outcome in self.feed_outcomes.items():
                lines.append(f"  {feed}: {outcome}")
        if self.errors:
            lines.append("Errors:")
            for err in self.errors:
                lines.append(f"  {err}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "sources_polled": self.sources_polled,
            "sources_success": self.sources_success,
            "sources_failed": self.sources_failed,
            "sources_not_modified": self.sources_not_modified,
            "items_added": self.items_added,
            "items_updated": self.items_updated,
            "items_expired": self.items_expired,
            "items_cap_pruned": self.items_cap_pruned,
            "clusters_count": self.clusters_count,
            "errors": self.errors,
            "feed_outcomes": self.feed_outcomes,
        }


class NewsPoller:
    """Operator-scheduled news feed poller per D16."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: Transport | None = None,
        resolver: Resolver | None = None,
        allowlist: set[str] | None = None,
        robots_cache: RobotsCache | None = None,
        mode: str = "fixture",
        lock_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.mode = mode
        self.allow_loopback = (mode == "fixture") or settings.allow_loopback_for_tests
        self.lock_path = lock_path or (settings.db_path.parent / "news_poll.lock")

        # Resolver & Transport
        self.resolver = resolver or SystemResolver()
        self.transport = transport or HttpxTransport(allow_loopback=self.allow_loopback)
        self.fetcher = SafeFetcher(self.transport, self.settings)

        self.robots_cache = robots_cache or RobotsCache()

        self.allowlist: set[str] | None
        if allowlist is not None:
            self.allowlist = allowlist
        elif self.mode == "fixture":
            self.allowlist = {"127.0.0.1", "localhost", "testserver", "fixture"}
        else:
            self.allowlist = None

        self.scheduler = Scheduler(
            self.settings,
            self.resolver,
            self.robots_cache,
            allowlist=self.allowlist,
            fetcher_ref=self.fetcher,
        )
        self.feed_adapter = FeedAdapter()

    def poll(
        self,
        *,
        sources_file: Path | None = None,
        force: bool = False,
        now: datetime | None = None,
    ) -> NewsPollReport:
        """Run a news polling cycle with locking, fetching, clustering, and retention."""
        if now is None:
            now = datetime.now(UTC)
        now_iso = now.isoformat()
        run_id = f"poll-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

        report = NewsPollReport(run_id=run_id, status="success")

        # 1. Acquire process lock
        lock = ProcessLock(self.lock_path, timeout_seconds=0.0)
        try:
            lock.acquire()
        except LockHeldError:
            report.status = "refused_lock_held"
            report.errors.append(f"Lock file {self.lock_path} is held by another active process")
            return report

        try:
            with Database(self.settings.db_path) as db, db.transaction() as conn:
                # 2. Sync sources if file exists
                src_path = sources_file or Path("data/news_sources.txt")
                if src_path.exists():
                    configs = load_news_sources_file(src_path)
                    sync_news_sources(conn, configs, allow_loopback=self.allow_loopback)

                # 3. Query due sources
                if force:
                    due_rows = conn.execute(
                        "SELECT id, feed_url_key, name, category, poll_interval_minutes, "
                        "images_allowed, etag, last_modified_header, consecutive_failures "
                        "FROM news_sources WHERE enabled = 1 ORDER BY id ASC"
                    ).fetchall()
                else:
                    due_rows = conn.execute(
                        "SELECT id, feed_url_key, name, category, poll_interval_minutes, "
                        "images_allowed, etag, last_modified_header, consecutive_failures "
                        "FROM news_sources "
                        "WHERE enabled = 1 AND (next_poll_at IS NULL OR next_poll_at <= ?) "
                        "ORDER BY id ASC",
                        (now_iso,),
                    ).fetchall()

                report.sources_polled = len(due_rows)

                doc_repo = DocumentRepository(conn)

                # 4. Poll each due feed
                for r in due_rows:
                    source_id = int(r["id"])
                    feed_url = str(r["feed_url_key"])
                    source_cat = str(r["category"])
                    interval = int(r["poll_interval_minutes"])
                    images_allowed = bool(r["images_allowed"])
                    etag = str(r["etag"]) if r["etag"] else None
                    last_mod = str(r["last_modified_header"]) if r["last_modified_header"] else None

                    # Check Scheduler approval (DNS, IP policy, robots, politeness)
                    approval = self.scheduler.approve(
                        feed_url,
                        kind="page",
                        etag=etag,
                        last_modified=last_mod,
                    )

                    if isinstance(approval, Denial):
                        report.sources_failed += 1
                        error_reason = approval.reason
                        conn.execute(
                            "UPDATE news_sources SET last_error_at = ?, last_error = ?, "
                            "consecutive_failures = consecutive_failures + 1 WHERE id = ?",
                            (now_iso, error_reason, source_id),
                        )
                        report.feed_outcomes[feed_url] = error_reason
                        report.errors.append(f"Feed {feed_url} denied: {error_reason}")
                        continue

                    # Approved -> Fetch
                    try:
                        fetch_res = self.fetcher.fetch_once(approval)
                    except Exception as exc:  # noqa: BLE001
                        report.sources_failed += 1
                        err_msg = f"fetch_exception: {exc}"
                        conn.execute(
                            "UPDATE news_sources SET last_error_at = ?, last_error = ?, "
                            "consecutive_failures = consecutive_failures + 1 WHERE id = ?",
                            (now_iso, err_msg, source_id),
                        )
                        report.feed_outcomes[feed_url] = err_msg
                        report.errors.append(f"Feed {feed_url} fetch error: {exc}")
                        self.scheduler.release(approval, status_code=0)
                        continue

                    if isinstance(fetch_res, Redirect):
                        report.sources_failed += 1
                        self.scheduler.release(approval, status_code=fetch_res.status_code)
                        report.feed_outcomes[feed_url] = f"redirect_{fetch_res.status_code}"
                        continue

                    self.scheduler.release(approval, status_code=fetch_res.status_code)

                    # Handle 304 Not Modified
                    if fetch_res.status_code == 304 or fetch_res.outcome == "not_modified":
                        report.sources_success += 1
                        report.sources_not_modified += 1
                        next_poll = (now + timedelta(minutes=interval)).isoformat()
                        conn.execute(
                            "UPDATE news_sources SET last_success_at = ?, last_error = NULL, "
                            "consecutive_failures = 0, next_poll_at = ? WHERE id = ?",
                            (now_iso, next_poll, source_id),
                        )
                        report.feed_outcomes[feed_url] = "304_not_modified"
                        continue

                    # Handle 200 OK
                    if fetch_res.status_code == 200 and fetch_res.outcome == "success":
                        new_etag = fetch_res.etag or fetch_res.headers.get("etag")
                        new_last_mod = fetch_res.last_modified or fetch_res.headers.get(
                            "last-modified"
                        )
                        adapter_res = self.feed_adapter.extract(
                            fetch_res.body,
                            fetch_res.url,
                            content_type=fetch_res.content_type,
                        )

                        if adapter_res.status == AdapterStatus.UNSUPPORTED:
                            report.sources_failed += 1
                            conn.execute(
                                "UPDATE news_sources SET last_error_at = ?, "
                                "last_error = 'parse_error', "
                                "consecutive_failures = consecutive_failures + 1 WHERE id = ?",
                                (now_iso, source_id),
                            )
                            report.feed_outcomes[feed_url] = "parse_error"
                            continue

                        next_poll = (now + timedelta(minutes=interval)).isoformat()
                        conn.execute(
                            "UPDATE news_sources SET etag = ?, last_modified_header = ?, "
                            "last_success_at = ?, last_error = NULL, consecutive_failures = 0, "
                            "next_poll_at = ? WHERE id = ?",
                            (new_etag, new_last_mod, now_iso, next_poll, source_id),
                        )
                        report.sources_success += 1
                        items_count = len(adapter_res.news_items)
                        report.feed_outcomes[feed_url] = f"200_ok_{items_count}_items"

                        # Upsert items
                        for item in adapter_res.news_items:
                            try:
                                norm_link = normalize(item.link, allow_loopback=self.allow_loopback)
                            except (UrlRejected, ValueError):
                                continue

                            item_id = hashlib.sha256(norm_link.key.encode("utf-8")).hexdigest()
                            summary = clean_summary(item.summary, max_chars=300)

                            if item.published_at:
                                pub_at = item.published_at
                                is_first_seen = 0
                            else:
                                pub_at = now_iso
                                is_first_seen = 1

                            item_cat = item.category or source_cat

                            # Candidate enqueue gated per D16
                            if (
                                images_allowed
                                and self.settings.news_images_enabled
                                and item.image_url
                            ):
                                logger.debug("Enqueued image candidate: %s", item.image_url)

                            # Check if item exists
                            existing = conn.execute(
                                "SELECT id FROM news_items WHERE id = ?", (item_id,)
                            ).fetchone()
                            if existing:
                                conn.execute(
                                    """
                                    UPDATE news_items
                                    SET title = ?, summary = ?, category = ?, updated_at = ?
                                    WHERE id = ?
                                    """,
                                    (item.title, summary, item_cat, now_iso, item_id),
                                )
                                report.items_updated += 1
                            else:
                                conn.execute(
                                    """
                                    INSERT INTO news_items (
                                        id, source_id, title, link, published_at,
                                        published_is_first_seen, summary, category, lang,
                                        first_seen_at, updated_at, status
                                    )
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'und', ?, ?, 'active')
                                    """,
                                    (
                                        item_id,
                                        source_id,
                                        item.title,
                                        norm_link.key,
                                        pub_at,
                                        is_first_seen,
                                        summary,
                                        item_cat,
                                        now_iso,
                                        now_iso,
                                    ),
                                )
                                report.items_added += 1

                            # Upsert document as doc_type = 'news' (no article body per D16)
                            doc_id = doc_repo.upsert_document(
                                news_item_id=item_id,
                                doc_type="news",
                                final_url=norm_link.key,
                                canonical_url=norm_link.key,
                                title=item.title,
                                description=summary,
                                headings="",
                                text_gz=None,
                                word_count=len(summary.split()),
                                status="active",
                                published_at=pub_at,
                                fetched_at=now_iso,
                            )
                            upsert_document_index(conn, doc_id)
                    else:
                        report.sources_failed += 1
                        err_stat = f"http_{fetch_res.status_code}_{fetch_res.outcome}"
                        conn.execute(
                            "UPDATE news_sources SET last_error_at = ?, last_error = ?, "
                            "consecutive_failures = consecutive_failures + 1 WHERE id = ?",
                            (now_iso, err_stat, source_id),
                        )
                        report.feed_outcomes[feed_url] = err_stat

                # 5. Run clustering
                clusters = cluster_news_items(conn, now=now)
                report.clusters_count = len(clusters)

                # 6. Run retention & caps
                expired, cap_pruned = apply_news_retention(
                    conn, now=now, max_days=14, max_active=500
                )
                report.items_expired = expired
                report.items_cap_pruned = cap_pruned

                # 7. Record poller metrics
                conn.execute(
                    """
                    INSERT INTO metrics (at, name, value, labels)
                    VALUES (?, 'news_items_added', ?, ?)
                    """,
                    (now_iso, float(report.items_added), f"sources_{report.sources_polled}"),
                )

                # 8. Verify index consistency
                verify_index_consistency(conn)

        finally:
            lock.release()

        return report
