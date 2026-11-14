"""Metrics collection, percentile calculation, and diagnostic reporting (Step 26).

Implements Step 26 requirements:
- URLs discovered, requests attempted/completed, robots blocks, documents stored/indexed,
  duplicates, HTTP errors by class, parser/adapter errors, crawl duration/throughput,
  bytes downloaded, storage used, index size.
- News: items per source, clusters, feed failures, newest item age per source.
- Images: stored, rejected by reason, bytes used.
- Entities: count and oldest retrieval.
- Serving: search and home latency percentiles (p50/p95/p99), results per query,
  empty-result rate, panel shown vs null rates, peak memory (psutil).
- CLI `search-engine report crawl|news|index|search|all` with readable and JSON output.
- All metrics persist across restarts in SQLite metrics table.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from search_engine.config import Settings
from search_engine.db import Database

logger = logging.getLogger(__name__)


def compute_percentile(values: Sequence[float], percentile: float) -> float:
    """Compute percentile using linear interpolation.

    Matches numpy / standard statistics percentile behavior.
    """
    if not values:
        return 0.0
    if percentile <= 0:
        return float(min(values))
    if percentile >= 100:
        return float(max(values))

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])

    k = (n - 1) * (percentile / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])

    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return float(round(d0 + d1, 3))


def record_metric(
    db: Database,
    name: str,
    value: float,
    labels: str = "",
    at: datetime | None = None,
) -> None:
    """Persist a single metric observation into SQLite metrics table."""
    at_iso = (at or datetime.now(UTC)).isoformat()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO metrics (at, name, value, labels) VALUES (?, ?, ?, ?)",
            (at_iso, name, float(value), labels),
        )


def record_metrics_batch(
    db: Database,
    items: Sequence[tuple[str, float, str]],
    at: datetime | None = None,
) -> None:
    """Persist multiple metric observations in a single transaction."""
    at_iso = (at or datetime.now(UTC)).isoformat()
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO metrics (at, name, value, labels) VALUES (?, ?, ?, ?)",
            [(at_iso, name, float(val), labels) for name, val, labels in items],
        )


@dataclass(frozen=True, slots=True)
class CrawlMetrics:
    urls_discovered: int
    requests_attempted: int
    requests_completed: int
    robots_blocks: int
    documents_stored: int
    documents_indexed: int
    duplicates_detected: int
    http_errors_by_class: dict[str, int]
    parser_and_adapter_errors: int
    crawl_duration_seconds: float
    crawl_throughput_pages_per_sec: float
    bytes_downloaded: int
    storage_used_bytes: int
    index_size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "urls_discovered": self.urls_discovered,
            "requests_attempted": self.requests_attempted,
            "requests_completed": self.requests_completed,
            "robots_blocks": self.robots_blocks,
            "documents_stored": self.documents_stored,
            "documents_indexed": self.documents_indexed,
            "duplicates_detected": self.duplicates_detected,
            "http_errors_by_class": self.http_errors_by_class,
            "parser_and_adapter_errors": self.parser_and_adapter_errors,
            "crawl_duration_seconds": round(self.crawl_duration_seconds, 2),
            "crawl_throughput_pages_per_sec": round(self.crawl_throughput_pages_per_sec, 2),
            "bytes_downloaded": self.bytes_downloaded,
            "storage_used_bytes": self.storage_used_bytes,
            "index_size_bytes": self.index_size_bytes,
        }

    def render(self) -> str:
        lines = [
            "--- CRAWL METRICS ---",
            f"  URLs Discovered:          {self.urls_discovered}",
            f"  Requests Attempted:       {self.requests_attempted}",
            f"  Requests Completed:       {self.requests_completed}",
            f"  Robots Blocks:            {self.robots_blocks}",
            f"  Documents Stored:         {self.documents_stored}",
            f"  Documents Indexed:        {self.documents_indexed}",
            f"  Duplicates Detected:      {self.duplicates_detected}",
            f"  Parser / Adapter Errors:  {self.parser_and_adapter_errors}",
            f"  Crawl Duration:           {self.crawl_duration_seconds:.1f} s",
            f"  Throughput:               {self.crawl_throughput_pages_per_sec:.2f} pages/s",
            f"  Bytes Downloaded:         {self.bytes_downloaded:,} bytes",
            f"  Total Storage Used:       {self.storage_used_bytes / (1024 * 1024):.2f} MB",
            f"  Database / Index Size:    {self.index_size_bytes / (1024 * 1024):.2f} MB",
        ]
        if self.http_errors_by_class:
            lines.append("  HTTP Errors by Class:")
            for code_cls, count in sorted(self.http_errors_by_class.items()):
                lines.append(f"    {code_cls}: {count}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class NewsMetrics:
    total_items: int
    items_per_source: dict[str, int]
    clusters_count: int
    feed_failures: dict[str, int]
    newest_item_age_seconds: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_items": self.total_items,
            "items_per_source": self.items_per_source,
            "clusters_count": self.clusters_count,
            "feed_failures": self.feed_failures,
            "newest_item_age_seconds": {
                k: round(v, 1) for k, v in self.newest_item_age_seconds.items()
            },
        }

    def render(self) -> str:
        lines = [
            "--- NEWS METRICS ---",
            f"  Total Active Items:       {self.total_items}",
            f"  Story Clusters:           {self.clusters_count}",
        ]
        if self.items_per_source:
            lines.append("  Items per Source:")
            for src, count in sorted(self.items_per_source.items()):
                age = self.newest_item_age_seconds.get(src)
                age_str = f" (newest: {age / 3600:.1f}h ago)" if age is not None else ""
                lines.append(f"    {src}: {count}{age_str}")
        if self.feed_failures:
            lines.append("  Failing Feeds:")
            for src, failures in sorted(self.feed_failures.items()):
                lines.append(f"    {src}: {failures} consecutive failures")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ImageMetrics:
    stored_count: int
    rejected_by_reason: dict[str, int]
    bytes_used: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stored_count": self.stored_count,
            "rejected_by_reason": self.rejected_by_reason,
            "bytes_used": self.bytes_used,
        }

    def render(self) -> str:
        lines = [
            "--- IMAGE METRICS ---",
            f"  Stored Thumbnails:        {self.stored_count}",
            f"  Storage Used:             {self.bytes_used / (1024 * 1024):.2f} MB",
        ]
        if self.rejected_by_reason:
            lines.append("  Rejections by Reason:")
            for reason, count in sorted(self.rejected_by_reason.items()):
                lines.append(f"    {reason}: {count}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class EntityMetrics:
    count: int
    oldest_retrieval_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "oldest_retrieval_at": self.oldest_retrieval_at,
        }

    def render(self) -> str:
        return "\n".join(
            [
                "--- ENTITY METRICS ---",
                f"  Total Entities:           {self.count}",
                f"  Oldest Retrieval:         {self.oldest_retrieval_at or 'none'}",
            ]
        )


@dataclass(frozen=True, slots=True)
class IndexMetrics:
    indexed_documents: int
    fts_row_count: int
    db_size_bytes: int
    index_version: int
    last_rebuild_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "indexed_documents": self.indexed_documents,
            "fts_row_count": self.fts_row_count,
            "db_size_bytes": self.db_size_bytes,
            "index_version": self.index_version,
            "last_rebuild_at": self.last_rebuild_at,
        }

    def render(self) -> str:
        return "\n".join(
            [
                "--- INDEX METRICS ---",
                f"  Indexed Documents:        {self.indexed_documents}",
                f"  FTS Index Rows:           {self.fts_row_count}",
                f"  Database File Size:       {self.db_size_bytes / (1024 * 1024):.2f} MB",
                f"  Index Version:            {self.index_version}",
                f"  Last Rebuild At:          {self.last_rebuild_at or 'unknown'}",
            ]
        )


@dataclass(frozen=True, slots=True)
class ServingMetrics:
    search_count: int
    search_latency_p50_ms: float
    search_latency_p95_ms: float
    search_latency_p99_ms: float
    home_count: int
    home_latency_p50_ms: float
    home_latency_p95_ms: float
    home_latency_p99_ms: float
    avg_results_per_query: float
    empty_result_rate: float
    panel_shown_rate: float
    panel_null_rate: float
    peak_memory_mb: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_count": self.search_count,
            "search_latency_p50_ms": round(self.search_latency_p50_ms, 2),
            "search_latency_p95_ms": round(self.search_latency_p95_ms, 2),
            "search_latency_p99_ms": round(self.search_latency_p99_ms, 2),
            "home_count": self.home_count,
            "home_latency_p50_ms": round(self.home_latency_p50_ms, 2),
            "home_latency_p95_ms": round(self.home_latency_p95_ms, 2),
            "home_latency_p99_ms": round(self.home_latency_p99_ms, 2),
            "avg_results_per_query": round(self.avg_results_per_query, 2),
            "empty_result_rate": round(self.empty_result_rate, 4),
            "panel_shown_rate": round(self.panel_shown_rate, 4),
            "panel_null_rate": round(self.panel_null_rate, 4),
            "peak_memory_mb": round(self.peak_memory_mb, 2),
        }

    def render(self) -> str:
        return "\n".join(
            [
                "--- SERVING METRICS ---",
                f"  Search Queries Recorded:  {self.search_count}",
                f"  Search Latency p50:       {self.search_latency_p50_ms:.2f} ms",
                f"  Search Latency p95:       {self.search_latency_p95_ms:.2f} ms",
                f"  Search Latency p99:       {self.search_latency_p99_ms:.2f} ms",
                f"  Home Requests Recorded:   {self.home_count}",
                f"  Home Latency p50:         {self.home_latency_p50_ms:.2f} ms",
                f"  Home Latency p95:         {self.home_latency_p95_ms:.2f} ms",
                f"  Home Latency p99:         {self.home_latency_p99_ms:.2f} ms",
                f"  Avg Results per Query:    {self.avg_results_per_query:.1f}",
                f"  Empty Result Rate:        {self.empty_result_rate * 100:.1f}%",
                f"  Panel Shown Rate:         {self.panel_shown_rate * 100:.1f}%",
                f"  Panel Null Rate:          {self.panel_null_rate * 100:.1f}%",
                f"  Resident Memory (RSS):    {self.peak_memory_mb:.1f} MB",
            ]
        )


@dataclass(frozen=True, slots=True)
class FullEngineReport:
    generated_at: str
    crawl: CrawlMetrics
    news: NewsMetrics
    images: ImageMetrics
    entities: EntityMetrics
    index: IndexMetrics
    serving: ServingMetrics
    system_memory_total_mb: float
    system_memory_available_mb: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "crawl": self.crawl.to_dict(),
            "news": self.news.to_dict(),
            "images": self.images.to_dict(),
            "entities": self.entities.to_dict(),
            "index": self.index.to_dict(),
            "serving": self.serving.to_dict(),
            "system_memory_total_mb": round(self.system_memory_total_mb, 2),
            "system_memory_available_mb": round(self.system_memory_available_mb, 2),
        }

    def render(self) -> str:
        lines = [
            f"=== INDEPENDENT SEARCH ENGINE DIAGNOSTIC REPORT ({self.generated_at}) ===",
            (
                f"System Memory: {self.system_memory_available_mb:.1f} MB free / "
                f"{self.system_memory_total_mb:.1f} MB total"
            ),
            "",
            self.crawl.render(),
            "",
            self.news.render(),
            "",
            self.images.render(),
            "",
            self.entities.render(),
            "",
            self.index.render(),
            "",
            self.serving.render(),
            "==========================================================================",
        ]
        return "\n".join(lines)


def _compute_dir_size(path: Path) -> int:
    """Recursively calculate total bytes in a directory, ignoring errors."""
    if not path.exists():
        return 0
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += _compute_dir_size(Path(entry.path))
            except OSError:
                pass
    except OSError:
        pass
    return total


def gather_crawl_metrics(db: Database, settings: Settings | None = None) -> CrawlMetrics:
    """Query database and storage to compile crawl metrics."""
    db_size = Path(db.path).stat().st_size if Path(db.path).exists() else 0
    raw_size = 0
    if settings is not None:
        raw_size = _compute_dir_size(Path(settings.raw_dir))

    with db.reader() as conn:
        urls_disc = int(conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0])
        reqs_comp = int(
            conn.execute(
                "SELECT COUNT(*) FROM urls WHERE status IN ('done', 'cached', 'unchanged')"
            ).fetchone()[0]
        )
        robots_blk = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM urls
                WHERE status = 'blocked' AND last_reason = 'robots_disallow'
                """
            ).fetchone()[0]
        )
        if robots_blk == 0:
            robots_blk = int(
                conn.execute(
                    "SELECT COUNT(*) FROM events WHERE reason = 'blocked_robots'"
                ).fetchone()[0]
            )

        docs_stored = int(
            conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'active'").fetchone()[0]
        )
        docs_indexed = int(
            conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'active' AND indexed_version > 0"
            ).fetchone()[0]
        )
        duplicates = int(
            conn.execute(
                "SELECT COUNT(*) FROM documents WHERE canonical_group_id IS NOT NULL"
            ).fetchone()[0]
        )
        adapter_errs = int(
            conn.execute(
                "SELECT COUNT(*) FROM events WHERE reason IN ('unsupported', 'failed')"
            ).fetchone()[0]
        )

        # HTTP error classification from events
        http_errors: dict[str, int] = {}
        for row in conn.execute(
            """
            SELECT detail, COUNT(*) FROM events
            WHERE reason = 'failed' AND detail LIKE 'http_%'
            GROUP BY detail
            """
        ):
            http_errors[str(row[0])] = int(row[1])

        # Crawl duration and throughput from metrics table
        dur_rows = [
            float(r[0])
            for r in conn.execute(
                "SELECT value FROM metrics WHERE name = 'crawl_duration_seconds'"
            ).fetchall()
        ]
        duration = sum(dur_rows)
        bytes_rows = [
            int(r[0])
            for r in conn.execute(
                "SELECT value FROM metrics WHERE name = 'crawl_bytes_downloaded'"
            ).fetchall()
        ]
        bytes_downloaded = sum(bytes_rows)

        # If metrics table has no bytes recorded, estimate from raw storage
        if bytes_downloaded == 0 and raw_size > 0:
            bytes_downloaded = raw_size

        throughput = (reqs_comp / duration) if duration > 0 else 0.0
        reqs_att = urls_disc

    return CrawlMetrics(
        urls_discovered=urls_disc,
        requests_attempted=reqs_att,
        requests_completed=reqs_comp,
        robots_blocks=robots_blk,
        documents_stored=docs_stored,
        documents_indexed=docs_indexed,
        duplicates_detected=duplicates,
        http_errors_by_class=http_errors,
        parser_and_adapter_errors=adapter_errs,
        crawl_duration_seconds=duration,
        crawl_throughput_pages_per_sec=throughput,
        bytes_downloaded=bytes_downloaded,
        storage_used_bytes=db_size + raw_size,
        index_size_bytes=db_size,
    )


def gather_news_metrics(db: Database, now: datetime | None = None) -> NewsMetrics:
    """Query database for active news items, source breakdowns, and feed health."""
    if now is None:
        now = datetime.now(UTC)

    items_per_source: dict[str, int] = {}
    feed_failures: dict[str, int] = {}
    newest_ages: dict[str, float] = {}
    total_items = 0

    with db.reader() as conn:
        clusters_count = int(conn.execute("SELECT COUNT(*) FROM news_clusters").fetchone()[0])

        # Breakdown per source
        for row in conn.execute(
            """
            SELECT s.name, COUNT(n.id)
            FROM news_sources s
            LEFT JOIN news_items n ON s.id = n.source_id AND n.status = 'active'
            GROUP BY s.id, s.name
            """
        ):
            src_name = str(row[0])
            count = int(row[1])
            items_per_source[src_name] = count
            total_items += count

        # Consecutive feed failures
        for row in conn.execute(
            """
            SELECT name, consecutive_failures FROM news_sources
            WHERE consecutive_failures > 0
            """
        ):
            feed_failures[str(row[0])] = int(row[1])

        # Newest item age per source
        for row in conn.execute(
            """
            SELECT s.name, MAX(n.published_at)
            FROM news_sources s
            JOIN news_items n ON s.id = n.source_id
            WHERE n.status = 'active'
            GROUP BY s.id, s.name
            """
        ):
            src_name = str(row[0])
            pub_str = str(row[1]) if row[1] else None
            if pub_str:
                try:
                    pub_dt = datetime.fromisoformat(pub_str)
                    age_sec = max(0.0, (now - pub_dt).total_seconds())
                    newest_ages[src_name] = age_sec
                except (ValueError, TypeError) as exc:
                    logger.debug("Failed to parse pub_at for %s: %s", src_name, exc)

    return NewsMetrics(
        total_items=total_items,
        items_per_source=items_per_source,
        clusters_count=clusters_count,
        feed_failures=feed_failures,
        newest_item_age_seconds=newest_ages,
    )


def gather_image_metrics(db: Database, settings: Settings | None = None) -> ImageMetrics:
    """Query image storage and rejections."""
    img_size = 0
    if settings is not None:
        img_size = _compute_dir_size(Path(settings.image_dir))

    rejections: dict[str, int] = {}
    with db.reader() as conn:
        stored_count = int(
            conn.execute("SELECT COUNT(*) FROM images WHERE status = 'active'").fetchone()[0]
        )
        for row in conn.execute(
            """
            SELECT detail, COUNT(*) FROM events
            WHERE reason = 'image_rejected'
            GROUP BY detail
            """
        ):
            rejections[str(row[0]) or "unspecified"] = int(row[1])

    return ImageMetrics(
        stored_count=stored_count,
        rejected_by_reason=rejections,
        bytes_used=img_size,
    )


def gather_entity_metrics(db: Database) -> EntityMetrics:
    """Query entity counts and oldest retrieval timestamp."""
    with db.reader() as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
        oldest_row = conn.execute("SELECT MIN(retrieved_at) FROM entities").fetchone()
        oldest = str(oldest_row[0]) if oldest_row and oldest_row[0] else None

    return EntityMetrics(
        count=count,
        oldest_retrieval_at=oldest,
    )


def gather_index_metrics(db: Database, settings: Settings | None = None) -> IndexMetrics:
    """Query index documents, FTS size, and version state."""
    db_size = Path(db.path).stat().st_size if Path(db.path).exists() else 0
    with db.reader() as conn:
        indexed_docs = int(
            conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'active' AND indexed_version > 0"
            ).fetchone()[0]
        )
        try:
            fts_rows = int(conn.execute("SELECT COUNT(*) FROM doc_fts").fetchone()[0])
        except Exception:
            fts_rows = 0

        # Query index state table
        v_row = conn.execute("SELECT value FROM index_state WHERE key = 'version'").fetchone()
        version = int(v_row[0]) if v_row else 1
        t_row = conn.execute(
            "SELECT value FROM index_state WHERE key = 'last_rebuild_at'"
        ).fetchone()
        last_rebuild = str(t_row[0]) if t_row else None

    return IndexMetrics(
        indexed_documents=indexed_docs,
        fts_row_count=fts_rows,
        db_size_bytes=db_size,
        index_version=version,
        last_rebuild_at=last_rebuild,
    )


def gather_serving_metrics(db: Database) -> ServingMetrics:
    """Aggregate search and home latency percentiles, empty rates, panel rates, and memory."""
    process = psutil.Process()
    try:
        mem_rss_mb = process.memory_info().rss / (1024 * 1024)
    except Exception:
        mem_rss_mb = 0.0

    with db.reader() as conn:
        # Search latency observations
        search_latencies = [
            float(r[0])
            for r in conn.execute(
                "SELECT value FROM metrics WHERE name = 'search_latency_ms'"
            ).fetchall()
        ]
        # Home latency observations
        home_latencies = [
            float(r[0])
            for r in conn.execute(
                "SELECT value FROM metrics WHERE name = 'home_latency_ms'"
            ).fetchall()
        ]
        # Result count observations
        result_counts = [
            float(r[0])
            for r in conn.execute(
                "SELECT value FROM metrics WHERE name = 'search_results_count'"
            ).fetchall()
        ]
        # Panel shown observations (1.0 = shown, 0.0 = null)
        panel_shown_obs = [
            float(r[0])
            for r in conn.execute(
                "SELECT value FROM metrics WHERE name = 'search_panel_shown'"
            ).fetchall()
        ]

    search_count = len(search_latencies)
    home_count = len(home_latencies)

    s_p50 = compute_percentile(search_latencies, 50)
    s_p95 = compute_percentile(search_latencies, 95)
    s_p99 = compute_percentile(search_latencies, 99)

    h_p50 = compute_percentile(home_latencies, 50)
    h_p95 = compute_percentile(home_latencies, 95)
    h_p99 = compute_percentile(home_latencies, 99)

    avg_results = (sum(result_counts) / len(result_counts)) if result_counts else 0.0
    empty_queries = sum(1 for c in result_counts if c == 0)
    empty_rate = (empty_queries / len(result_counts)) if result_counts else 0.0

    if panel_shown_obs:
        panels_shown = sum(1 for p in panel_shown_obs if p > 0.0)
        panel_shown_rate = panels_shown / len(panel_shown_obs)
        panel_null_rate = 1.0 - panel_shown_rate
    else:
        panel_shown_rate = 0.0
        panel_null_rate = 0.0

    return ServingMetrics(
        search_count=search_count,
        search_latency_p50_ms=s_p50,
        search_latency_p95_ms=s_p95,
        search_latency_p99_ms=s_p99,
        home_count=home_count,
        home_latency_p50_ms=h_p50,
        home_latency_p95_ms=h_p95,
        home_latency_p99_ms=h_p99,
        avg_results_per_query=avg_results,
        empty_result_rate=empty_rate,
        panel_shown_rate=panel_shown_rate,
        panel_null_rate=panel_null_rate,
        peak_memory_mb=mem_rss_mb,
    )


def gather_all_metrics(db: Database, settings: Settings | None = None) -> FullEngineReport:
    """Compile diagnostic report covering crawl, news, images, entities, index, and serving."""
    now_iso = datetime.now(UTC).isoformat()
    try:
        mem = psutil.virtual_memory()
        mem_total = mem.total / (1024 * 1024)
        mem_avail = mem.available / (1024 * 1024)
    except Exception:
        mem_total = 0.0
        mem_avail = 0.0

    crawl = gather_crawl_metrics(db, settings)
    news = gather_news_metrics(db)
    images = gather_image_metrics(db, settings)
    entities = gather_entity_metrics(db)
    index = gather_index_metrics(db, settings)
    serving = gather_serving_metrics(db)

    return FullEngineReport(
        generated_at=now_iso,
        crawl=crawl,
        news=news,
        images=images,
        entities=entities,
        index=index,
        serving=serving,
        system_memory_total_mb=mem_total,
        system_memory_available_mb=mem_avail,
    )
