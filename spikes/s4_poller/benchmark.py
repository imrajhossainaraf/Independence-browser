"""SQLite WAL concurrency benchmark for news poller and read server (Spike S4).

Simulates high-frequency concurrent writes from news poller while an API server
reads home page queries, measuring reader latency percentiles (p50, p95, p99)
and proving zero lock contention under WAL mode.
"""

from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from search_engine.db import Database


@dataclass
class ConcurrencyStats:
    total_reads: int = 0
    total_writes: int = 0
    read_latencies_ms: list[float] = field(default_factory=list)
    read_errors: int = 0
    write_errors: int = 0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.read_latencies_ms) if self.read_latencies_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.read_latencies_ms:
            return 0.0
        sorted_l = sorted(self.read_latencies_ms)
        idx = int(0.95 * len(sorted_l))
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def p99_ms(self) -> float:
        if not self.read_latencies_ms:
            return 0.0
        sorted_l = sorted(self.read_latencies_ms)
        idx = int(0.99 * len(sorted_l))
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def max_ms(self) -> float:
        return max(self.read_latencies_ms) if self.read_latencies_ms else 0.0


def run_poller_server_benchmark(
    db: Database,
    *,
    duration_seconds: float = 2.0,
    write_batch_size: int = 10,
) -> ConcurrencyStats:
    """Run concurrent writer (poller) and reader (server) for duration_seconds."""
    stats = ConcurrencyStats()
    stop_event = threading.Event()

    # Pre-populate sample news data
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO news_sources "
            "(id, feed_url_key, name, category, poll_interval_minutes) "
            "VALUES (1, 'https://example.com/rss', 'Example News', 'tech', 30)"
        )
        for i in range(50):
            conn.execute(
                "INSERT OR IGNORE INTO news_items "
                "(id, source_id, title, link, published_at, summary, category, first_seen_at, updated_at) "
                "VALUES (?, 1, ?, ?, ?, ?, 'tech', ?, ?)",
                (
                    f"seed-{i}",
                    f"Sample Headline {i}",
                    f"https://example.com/item/{i}",
                    datetime.now(UTC).isoformat(),
                    "Brief summary text for benchmark testing.",
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def writer_loop() -> None:
        item_counter = 1000
        while not stop_event.is_set():
            try:
                with db.transaction() as conn:
                    # Simulate polling batch insertion
                    cluster_time = datetime.now(UTC).isoformat()
                    cur = conn.execute(
                        "INSERT INTO news_clusters (created_at, updated_at) VALUES (?, ?)",
                        (cluster_time, cluster_time),
                    )
                    cluster_id = cur.lastrowid
                    for _ in range(write_batch_size):
                        item_counter += 1
                        now_str = datetime.now(UTC).isoformat()
                        conn.execute(
                            "INSERT INTO news_items "
                            "(id, source_id, cluster_id, title, link, published_at, "
                            "summary, category, first_seen_at, updated_at) "
                            "VALUES (?, 1, ?, ?, ?, ?, 'Tech summary', 'tech', ?, ?)",
                            (
                                f"item-{item_counter}",
                                cluster_id,
                                f"Breaking News Story {item_counter}",
                                f"https://example.com/item/{item_counter}",
                                now_str,
                                now_str,
                                now_str,
                            ),
                        )
                stats.total_writes += write_batch_size
            except Exception:
                stats.write_errors += 1
            time.sleep(0.01)

    def reader_loop() -> None:
        while not stop_event.is_set():
            t0 = time.perf_counter()
            try:
                with db.reader() as conn:
                    # Simulate reading home page top stories and recent items
                    cur = conn.execute(
                        """
                        SELECT ni.id, ni.title, ni.published_at, ni.summary, ns.name
                        FROM news_items ni
                        JOIN news_sources ns ON ni.source_id = ns.id
                        WHERE ni.status = 'active'
                        ORDER BY ni.published_at DESC
                        LIMIT 20
                        """
                    )
                    rows = cur.fetchall()
                    _ = len(rows)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                stats.read_latencies_ms.append(elapsed_ms)
                stats.total_reads += 1
            except Exception:
                stats.read_errors += 1
            time.sleep(0.005)

    w_thread = threading.Thread(target=writer_loop, daemon=True)
    r_thread = threading.Thread(target=reader_loop, daemon=True)

    w_thread.start()
    r_thread.start()

    time.sleep(duration_seconds)
    stop_event.set()

    w_thread.join(timeout=2.0)
    r_thread.join(timeout=2.0)

    return stats
