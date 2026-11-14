"""SQLite-backed crawl frontier (decision D8, D12, Step 15).

Manages discovered URLs with leases, retries, exponential backoff, priority,
depth tracking, host caps, global caps, allowlist/blocklist filtering, and crash recovery.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from search_engine.config import Settings
from search_engine.crawler.blocklist import Blocklist
from search_engine.crawler.normalize import NormalizedUrl, normalize
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.repositories import HostRepository


def _now(dt: datetime | None = None) -> str:
    return (dt or datetime.now(UTC)).isoformat()


@dataclass
class FrontierConfig:
    """Limits and configuration for the crawl frontier."""

    max_depth: int = 3
    max_pages: int = 500
    max_pages_per_host: int = 200
    lease_timeout_seconds: float = 300.0
    max_retries: int = 3
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 60.0
    runtime_limit_seconds: float | None = None
    start_time: datetime | None = None
    allow_loopback: bool = False
    allowlist_mode: bool = True
    allowlist_domains: set[str] = field(default_factory=set)
    allowlist_hosts: set[str] = field(default_factory=set)
    blocklist: Blocklist | set[str] | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        allowlist_domains: set[str] | frozenset[str] | None = None,
        allowlist_hosts: set[str] | frozenset[str] | None = None,
        blocklist: Blocklist | set[str] | None = None,
    ) -> FrontierConfig:
        return cls(
            max_depth=settings.max_depth,
            max_pages=settings.max_pages,
            max_pages_per_host=settings.max_pages_per_host,
            max_retries=settings.max_retries,
            backoff_base_seconds=settings.backoff_base_seconds,
            backoff_max_seconds=settings.backoff_max_seconds,
            allow_loopback=settings.allow_loopback_for_tests,
            allowlist_mode=settings.allowlist_mode,
            allowlist_domains=set(allowlist_domains) if allowlist_domains else set(),
            allowlist_hosts=set(allowlist_hosts) if allowlist_hosts else set(),
            blocklist=blocklist,
        )


@dataclass(frozen=True, slots=True)
class QueuedUrl:
    """A URL leased from the frontier for fetching."""

    id: int
    url_key: str
    host_id: int
    host: str
    registered_domain: str
    depth: int
    priority: int
    source: str
    retry_count: int
    parent_url_id: int | None = None
    lease_expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class FrontierEntry:
    """Snapshot of a URL in the frontier database."""

    id: int
    url_key: str
    host_id: int
    host: str
    registered_domain: str
    status: str
    depth: int
    priority: int
    source: str
    parent_url_id: int | None
    retry_count: int
    lease_expires_at: str | None
    next_attempt_at: str | None
    last_reason: str | None
    discovered_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class AddResult:
    """Result of adding or rediscovering a URL in the frontier."""

    url_id: int
    status: str
    created: bool
    reason: str | None = None


def calculate_priority(source: str, depth: int) -> int:
    """Compute crawl priority: seeds (100) > redirects (90) > feeds/sitemaps (80) > links."""
    if source == "seed":
        return 100
    if source == "redirect":
        return 90
    if source in ("sitemap", "feed"):
        return 80
    if source == "link":
        return max(0, 50 - depth * 10)
    return 0


class Frontier:
    """SQLite-backed queue managing URLs for the web crawler."""

    def __init__(self, conn: sqlite3.Connection, config: FrontierConfig | None = None) -> None:
        self._conn = conn
        self.config = config or FrontierConfig()
        self._host_repo = HostRepository(conn)

    def is_blocklisted(self, norm: NormalizedUrl) -> str | None:
        """Check if a normalized URL matches the blocklist."""
        if self.config.blocklist is None:
            return None
        if isinstance(self.config.blocklist, Blocklist):
            return self.config.blocklist.reason(norm)
        if norm.host in self.config.blocklist:
            return "host"
        if norm.registered_domain in self.config.blocklist:
            return "domain"
        return None

    def is_allowlisted(self, norm: NormalizedUrl) -> bool:
        """Check if a normalized URL falls within the allowed scope (D12)."""
        if not self.config.allowlist_mode:
            return True
        if not self.config.allowlist_domains and not self.config.allowlist_hosts:
            return True
        return (norm.host in self.config.allowlist_hosts) or (
            norm.registered_domain in self.config.allowlist_domains
        )

    def _is_host_capped(self, host_id: int) -> bool:
        """Check if a host has reached or exceeded max_pages_per_host."""
        row = self._conn.execute(
            "SELECT pages_stored FROM hosts WHERE id = ?", (host_id,)
        ).fetchone()
        if row is None:
            return False
        return int(row[0]) >= self.config.max_pages_per_host

    def _is_global_capped(self) -> bool:
        """Check if total stored/done pages across all hosts has reached max_pages."""
        row = self._conn.execute("SELECT COUNT(*) FROM urls WHERE status = 'done'").fetchone()
        if row is None:
            return False
        return int(row[0]) >= self.config.max_pages

    def add_seed(
        self,
        url: str | NormalizedUrl,
        *,
        priority: int = 100,
        now: datetime | None = None,
    ) -> AddResult:
        """Add a seed URL to the frontier at depth 0."""
        iso_now = _now(now)
        if isinstance(url, str):
            norm = normalize(url, allow_loopback=self.config.allow_loopback)
        else:
            norm = url

        host_id = self._host_repo.ensure(norm.host, norm.registered_domain, now=iso_now)

        block_reason = self.is_blocklisted(norm)
        if block_reason is not None:
            self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'blocked', 0, ?, 'seed', ?, ?, ?) "
                "ON CONFLICT(url_key) DO UPDATE SET "
                "status = 'blocked', last_reason = excluded.last_reason, "
                "updated_at = excluded.updated_at",
                (norm.key, host_id, priority, f"blocked:{block_reason}", iso_now, iso_now),
            )
            row = self._conn.execute(
                "SELECT id FROM urls WHERE url_key = ?", (norm.key,)
            ).fetchone()
            return AddResult(int(row[0]), "blocked", created=True, reason=f"blocked:{block_reason}")

        # Check existing
        existing = self._conn.execute(
            "SELECT id, status, depth FROM urls WHERE url_key = ?", (norm.key,)
        ).fetchone()
        if existing is not None:
            existing_id = int(existing[0])
            existing_status = str(existing[1])
            existing_depth = int(existing[2])
            if existing_depth > 0:
                self._conn.execute(
                    "UPDATE urls SET depth = 0, priority = ?, source = 'seed', updated_at = ? "
                    "WHERE id = ?",
                    (priority, iso_now, existing_id),
                )
            return AddResult(existing_id, existing_status, created=False)

        cursor = self._conn.execute(
            "INSERT INTO urls "
            "(url_key, host_id, status, depth, priority, source, discovered_at, updated_at) "
            "VALUES (?, ?, 'pending', 0, ?, 'seed', ?, ?)",
            (norm.key, host_id, priority, iso_now, iso_now),
        )
        url_id = cursor.lastrowid or int(
            self._conn.execute("SELECT id FROM urls WHERE url_key = ?", (norm.key,)).fetchone()[0]
        )
        return AddResult(url_id, "pending", created=True)

    def add_discovered_url(
        self,
        url: str | NormalizedUrl,
        *,
        source: str = "link",
        parent_url_id: int | None = None,
        depth: int = 1,
        nofollow: bool = False,
        now: datetime | None = None,
    ) -> AddResult:
        """Add a discovered URL (link, sitemap, feed, redirect) to the frontier."""
        iso_now = _now(now)
        if isinstance(url, str):
            try:
                norm = normalize(url, allow_loopback=self.config.allow_loopback)
            except UrlRejected as exc:
                return AddResult(
                    -1, "rejected", created=False, reason=f"rejected:{exc.reason.value}"
                )
        else:
            norm = url

        host_id = self._host_repo.ensure(norm.host, norm.registered_domain, now=iso_now)

        # Record link relationship if parent is known
        if parent_url_id is not None:
            self._conn.execute(
                "INSERT OR IGNORE INTO links (from_url_id, to_url_key, nofollow, discovered_at) "
                "VALUES (?, ?, ?, ?)",
                (parent_url_id, norm.key, 1 if nofollow else 0, iso_now),
            )

        # Check existing
        existing = self._conn.execute(
            "SELECT id, status, depth, priority, last_reason FROM urls WHERE url_key = ?",
            (norm.key,),
        ).fetchone()
        if existing is not None:
            existing_id = int(existing[0])
            existing_status = str(existing[1])
            existing_depth = int(existing[2])
            last_reason = existing[4]

            # Lower depth if discovered via a shorter path
            if depth < existing_depth:
                new_priority = calculate_priority(source, depth)
                if existing_status in ("pending", "retry"):
                    self._conn.execute(
                        "UPDATE urls SET depth = ?, priority = ?, parent_url_id = ?, "
                        "updated_at = ? WHERE id = ?",
                        (depth, new_priority, parent_url_id, iso_now, existing_id),
                    )
                elif (
                    existing_status == "skipped"
                    and last_reason == "depth_exceeded"
                    and depth <= self.config.max_depth
                    and self.is_blocklisted(norm) is None
                    and self.is_allowlisted(norm)
                    and not self._is_host_capped(host_id)
                    and not self._is_global_capped()
                    and not nofollow
                ):
                    self._conn.execute(
                        "UPDATE urls SET status = 'pending', depth = ?, priority = ?, "
                        "parent_url_id = ?, last_reason = NULL, updated_at = ? WHERE id = ?",
                        (depth, new_priority, parent_url_id, iso_now, existing_id),
                    )
                    existing_status = "pending"
                else:
                    self._conn.execute(
                        "UPDATE urls SET depth = ?, updated_at = ? WHERE id = ?",
                        (depth, iso_now, existing_id),
                    )
            return AddResult(existing_id, existing_status, created=False)

        # Check policy and limit constraints for brand new URLs
        block_reason = self.is_blocklisted(norm)
        if block_reason is not None:
            reason = f"blocked:{block_reason}"
            cursor = self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "parent_url_id, last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'blocked', ?, 0, ?, ?, ?, ?, ?)",
                (norm.key, host_id, depth, source, parent_url_id, reason, iso_now, iso_now),
            )
            new_id = cursor.lastrowid or 0
            return AddResult(int(new_id), "blocked", created=True, reason=reason)

        if not self.is_allowlisted(norm):
            reason = "outside_allowlist"
            cursor = self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "parent_url_id, last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'skipped', ?, 0, ?, ?, ?, ?, ?)",
                (norm.key, host_id, depth, source, parent_url_id, reason, iso_now, iso_now),
            )
            new_id = cursor.lastrowid or 0
            return AddResult(int(new_id), "skipped", created=True, reason=reason)

        if nofollow:
            reason = "nofollow"
            cursor = self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "parent_url_id, last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'skipped', ?, 0, ?, ?, ?, ?, ?)",
                (norm.key, host_id, depth, source, parent_url_id, reason, iso_now, iso_now),
            )
            new_id = cursor.lastrowid or 0
            return AddResult(int(new_id), "skipped", created=True, reason=reason)

        if depth > self.config.max_depth:
            reason = "depth_exceeded"
            cursor = self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "parent_url_id, last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'skipped', ?, 0, ?, ?, ?, ?, ?)",
                (norm.key, host_id, depth, source, parent_url_id, reason, iso_now, iso_now),
            )
            new_id = cursor.lastrowid or 0
            return AddResult(int(new_id), "skipped", created=True, reason=reason)

        if self._is_host_capped(host_id):
            reason = "host_cap_reached"
            cursor = self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "parent_url_id, last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'skipped', ?, 0, ?, ?, ?, ?, ?)",
                (norm.key, host_id, depth, source, parent_url_id, reason, iso_now, iso_now),
            )
            new_id = cursor.lastrowid or 0
            return AddResult(int(new_id), "skipped", created=True, reason=reason)

        if self._is_global_capped():
            reason = "global_cap_reached"
            cursor = self._conn.execute(
                "INSERT INTO urls (url_key, host_id, status, depth, priority, source, "
                "parent_url_id, last_reason, discovered_at, updated_at) "
                "VALUES (?, ?, 'skipped', ?, 0, ?, ?, ?, ?, ?)",
                (norm.key, host_id, depth, source, parent_url_id, reason, iso_now, iso_now),
            )
            new_id = cursor.lastrowid or 0
            return AddResult(int(new_id), "skipped", created=True, reason=reason)

        # Eligible URL enters pending queue
        priority = calculate_priority(source, depth)
        cursor = self._conn.execute(
            "INSERT INTO urls (url_key, host_id, status, depth, priority, source, parent_url_id, "
            "discovered_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
            (norm.key, host_id, depth, priority, source, parent_url_id, iso_now, iso_now),
        )
        new_id = cursor.lastrowid or 0
        return AddResult(int(new_id), "pending", created=True)

    def add_sitemap_urls(
        self,
        urls: list[str | NormalizedUrl],
        *,
        parent_url_id: int | None = None,
        depth: int = 1,
        now: datetime | None = None,
    ) -> list[AddResult]:
        """Add URLs discovered from a sitemap."""
        return [
            self.add_discovered_url(
                u,
                source="sitemap",
                parent_url_id=parent_url_id,
                depth=depth,
                now=now,
            )
            for u in urls
        ]

    def add_feed_urls(
        self,
        urls: list[str | NormalizedUrl],
        *,
        parent_url_id: int | None = None,
        depth: int = 1,
        now: datetime | None = None,
    ) -> list[AddResult]:
        """Add URLs discovered from an RSS/Atom feed."""
        return [
            self.add_discovered_url(
                u,
                source="feed",
                parent_url_id=parent_url_id,
                depth=depth,
                now=now,
            )
            for u in urls
        ]

    def lease_next(
        self,
        batch_size: int = 1,
        *,
        active_hosts: set[str] | None = None,
        now: datetime | None = None,
    ) -> list[QueuedUrl]:
        """Lease eligible URLs ordered by priority DESC, depth ASC, id ASC.

        Recovers expired leases first. Enforces per-host politeness (at most one
        URL per host leased in the batch, excluding active hosts) and limits.
        """
        current_dt = now or datetime.now(UTC)
        iso_now = _now(current_dt)

        # Recover any leases that timed out
        self.recover_crashed_leases(now=current_dt)

        # Check runtime limits
        if self.config.runtime_limit_seconds is not None and self.config.start_time is not None:
            elapsed = (current_dt - self.config.start_time).total_seconds()
            if elapsed >= self.config.runtime_limit_seconds:
                return []

        # Check global limit
        if self._is_global_capped():
            return []

        lease_expiry = (
            current_dt + timedelta(seconds=self.config.lease_timeout_seconds)
        ).isoformat()
        excluded_hosts = set(active_hosts) if active_hosts else set()
        leased_urls: list[QueuedUrl] = []

        # Query candidates that are pending, or retrying and due
        candidates = self._conn.execute(
            "SELECT urls.id, urls.url_key, urls.host_id, hosts.host, "
            "hosts.registered_domain, urls.depth, urls.priority, urls.source, "
            "urls.retry_count, urls.parent_url_id, hosts.pages_stored "
            "FROM urls "
            "JOIN hosts ON urls.host_id = hosts.id "
            "WHERE (urls.status = 'pending' "
            "       OR (urls.status = 'retry' AND "
            "           (urls.next_attempt_at IS NULL OR urls.next_attempt_at <= ?))) "
            "ORDER BY urls.priority DESC, urls.depth ASC, urls.id ASC",
            (iso_now,),
        ).fetchall()

        for row in candidates:
            (
                url_id,
                url_key,
                host_id,
                host,
                reg_domain,
                depth,
                priority,
                source,
                retry_count,
                parent_url_id,
                pages_stored,
            ) = (
                int(row[0]),
                str(row[1]),
                int(row[2]),
                str(row[3]),
                str(row[4]),
                int(row[5]),
                int(row[6]),
                str(row[7]),
                int(row[8]),
                row[9],
                int(row[10]),
            )

            # Skip host if cap reached
            if pages_stored >= self.config.max_pages_per_host:
                self._conn.execute(
                    "UPDATE urls SET status = 'skipped', last_reason = 'host_cap_reached', "
                    "updated_at = ? WHERE id = ?",
                    (iso_now, url_id),
                )
                continue

            # Respect per-host politeness (at most 1 concurrent fetch per host)
            if host in excluded_hosts:
                continue

            # Mark as leased
            self._conn.execute(
                "UPDATE urls SET status = 'leased', lease_expires_at = ?, "
                "updated_at = ? WHERE id = ?",
                (lease_expiry, iso_now, url_id),
            )
            excluded_hosts.add(host)
            leased_urls.append(
                QueuedUrl(
                    id=url_id,
                    url_key=url_key,
                    host_id=host_id,
                    host=host,
                    registered_domain=reg_domain,
                    depth=depth,
                    priority=priority,
                    source=source,
                    retry_count=retry_count,
                    parent_url_id=int(parent_url_id) if parent_url_id is not None else None,
                    lease_expires_at=lease_expiry,
                )
            )

            if len(leased_urls) >= batch_size:
                break

        return leased_urls

    def complete(self, url_id: int, *, now: datetime | None = None) -> None:
        """Mark a leased URL as done and increment pages_stored on its host."""
        iso_now = _now(now)
        self._conn.execute(
            "UPDATE urls SET status = 'done', lease_expires_at = NULL, updated_at = ? WHERE id = ?",
            (iso_now, url_id),
        )
        self._conn.execute(
            "UPDATE hosts SET pages_stored = pages_stored + 1 WHERE id = "
            "(SELECT host_id FROM urls WHERE id = ?)",
            (url_id,),
        )

    def retry(
        self,
        url_id: int,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Transition a URL to retry with exponential backoff, or fail permanently if exhausted."""
        current_dt = now or datetime.now(UTC)
        iso_now = _now(current_dt)

        row = self._conn.execute("SELECT retry_count FROM urls WHERE id = ?", (url_id,)).fetchone()
        if row is None:
            return False

        retry_count = int(row[0]) + 1
        if retry_count > self.config.max_retries:
            self.fail_permanent(
                url_id,
                reason=reason or "max_retries_exceeded",
                now=current_dt,
            )
            return False

        delay = min(
            self.config.backoff_max_seconds,
            self.config.backoff_base_seconds * (2 ** (retry_count - 1)),
        )
        next_attempt = (current_dt + timedelta(seconds=delay)).isoformat()
        self._conn.execute(
            "UPDATE urls SET status = 'retry', retry_count = ?, next_attempt_at = ?, "
            "lease_expires_at = NULL, last_reason = ?, updated_at = ? WHERE id = ?",
            (retry_count, next_attempt, reason, iso_now, url_id),
        )
        return True

    def fail_permanent(
        self,
        url_id: int,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Mark a URL as failed_permanent."""
        iso_now = _now(now)
        self._conn.execute(
            "UPDATE urls SET status = 'failed_permanent', lease_expires_at = NULL, "
            "last_reason = ?, updated_at = ? WHERE id = ?",
            (reason, iso_now, url_id),
        )

    def skip(
        self,
        url_id: int,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Mark a URL as skipped."""
        iso_now = _now(now)
        self._conn.execute(
            "UPDATE urls SET status = 'skipped', lease_expires_at = NULL, "
            "last_reason = ?, updated_at = ? WHERE id = ?",
            (reason, iso_now, url_id),
        )

    def block(
        self,
        url_id: int,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Mark a URL as blocked."""
        iso_now = _now(now)
        self._conn.execute(
            "UPDATE urls SET status = 'blocked', lease_expires_at = NULL, "
            "last_reason = ?, updated_at = ? WHERE id = ?",
            (reason, iso_now, url_id),
        )

    def recover_crashed_leases(self, now: datetime | None = None) -> int:
        """Reset timed-out leased URLs back to pending."""
        iso_now = _now(now)
        cursor = self._conn.execute(
            "UPDATE urls SET status = 'pending', lease_expires_at = NULL, updated_at = ? "
            "WHERE status = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
            (iso_now, iso_now),
        )
        return cursor.rowcount

    def reset_leases(self, now: datetime | None = None) -> int:
        """Reset all leased URLs back to pending (for restart/resumption)."""
        iso_now = _now(now)
        cursor = self._conn.execute(
            "UPDATE urls SET status = 'pending', lease_expires_at = NULL, updated_at = ? "
            "WHERE status = 'leased'",
            (iso_now,),
        )
        return cursor.rowcount

    def get(self, url_key: str) -> FrontierEntry | None:
        """Fetch a frontier entry by its normalized URL key."""
        row = self._conn.execute(
            "SELECT urls.id, urls.url_key, urls.host_id, hosts.host, hosts.registered_domain, "
            "urls.status, urls.depth, urls.priority, urls.source, urls.parent_url_id, "
            "urls.retry_count, urls.lease_expires_at, urls.next_attempt_at, urls.last_reason, "
            "urls.discovered_at, urls.updated_at "
            "FROM urls "
            "JOIN hosts ON urls.host_id = hosts.id "
            "WHERE urls.url_key = ?",
            (url_key,),
        ).fetchone()
        if row is None:
            return None
        return FrontierEntry(
            id=int(row[0]),
            url_key=str(row[1]),
            host_id=int(row[2]),
            host=str(row[3]),
            registered_domain=str(row[4]),
            status=str(row[5]),
            depth=int(row[6]),
            priority=int(row[7]),
            source=str(row[8]),
            parent_url_id=int(row[9]) if row[9] is not None else None,
            retry_count=int(row[10]),
            lease_expires_at=row[11],
            next_attempt_at=row[12],
            last_reason=row[13],
            discovered_at=str(row[14]),
            updated_at=str(row[15]),
        )

    def get_by_id(self, url_id: int) -> FrontierEntry | None:
        """Fetch a frontier entry by URL id."""
        row = self._conn.execute(
            "SELECT urls.id, urls.url_key, urls.host_id, hosts.host, hosts.registered_domain, "
            "urls.status, urls.depth, urls.priority, urls.source, urls.parent_url_id, "
            "urls.retry_count, urls.lease_expires_at, urls.next_attempt_at, urls.last_reason, "
            "urls.discovered_at, urls.updated_at "
            "FROM urls "
            "JOIN hosts ON urls.host_id = hosts.id "
            "WHERE urls.id = ?",
            (url_id,),
        ).fetchone()
        if row is None:
            return None
        return FrontierEntry(
            id=int(row[0]),
            url_key=str(row[1]),
            host_id=int(row[2]),
            host=str(row[3]),
            registered_domain=str(row[4]),
            status=str(row[5]),
            depth=int(row[6]),
            priority=int(row[7]),
            source=str(row[8]),
            parent_url_id=int(row[9]) if row[9] is not None else None,
            retry_count=int(row[10]),
            lease_expires_at=row[11],
            next_attempt_at=row[12],
            last_reason=row[13],
            discovered_at=str(row[14]),
            updated_at=str(row[15]),
        )

    def get_links(self, from_url_id: int) -> list[tuple[str, bool]]:
        """Return list of (to_url_key, nofollow) links recorded from this URL."""
        rows = self._conn.execute(
            "SELECT to_url_key, nofollow FROM links WHERE from_url_id = ? ORDER BY id ASC",
            (from_url_id,),
        ).fetchall()
        return [(str(r[0]), bool(r[1])) for r in rows]

    def has_pending(self, now: datetime | None = None) -> bool:
        """Check if any work remains in pending, retry, or leased states."""
        iso_now = _now(now)
        row = self._conn.execute(
            "SELECT 1 FROM urls WHERE status = 'pending' "
            "OR (status = 'retry' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)) "
            "OR status = 'leased' LIMIT 1",
            (iso_now,),
        ).fetchone()
        return row is not None

    def stats(self) -> dict[str, int]:
        """Return total counts of URLs grouped by status."""
        rows = self._conn.execute("SELECT status, COUNT(*) FROM urls GROUP BY status").fetchall()
        counts = {str(r[0]): int(r[1]) for r in rows}
        return {
            "pending": counts.get("pending", 0),
            "leased": counts.get("leased", 0),
            "done": counts.get("done", 0),
            "retry": counts.get("retry", 0),
            "failed_permanent": counts.get("failed_permanent", 0),
            "blocked": counts.get("blocked", 0),
            "skipped": counts.get("skipped", 0),
            "total": sum(counts.values()),
        }
