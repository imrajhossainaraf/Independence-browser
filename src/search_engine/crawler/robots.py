"""Robots.txt parsing, policies, and caching (decision D3, RFC 9309).

Uses Protego behind a project-owned RobotsPolicy interface.
Caches per scheme, host, and port:
- 2xx: parsed (TTL 24 hours)
- 4xx: allow all (TTL 24 hours)
- 5xx / timeout / network failure / refused redirect: disallow all (TTL 10 minutes)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from protego import Protego

PARSED_TTL = timedelta(hours=24)
ALLOW_ALL_TTL = timedelta(hours=24)
DISALLOW_ALL_TTL = timedelta(minutes=10)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RobotsPolicy(Protocol):
    """Protocol for checking robots.txt rules and crawl delay."""

    def can_fetch(self, url: str, user_agent: str) -> bool: ...

    def crawl_delay(self, user_agent: str) -> float | None: ...


class ProtegoRobotsPolicy:
    """RobotsPolicy adapter wrapping Protego."""

    def __init__(self, content: str) -> None:
        self._protego = Protego.parse(content=content)

    def can_fetch(self, url: str, user_agent: str) -> bool:
        return bool(self._protego.can_fetch(url, user_agent))

    def crawl_delay(self, user_agent: str) -> float | None:
        delay = self._protego.crawl_delay(user_agent)
        return float(delay) if delay is not None else None


class AllowAllRobotsPolicy:
    """Policy returned on 4xx responses (allows all crawling)."""

    def can_fetch(self, url: str, user_agent: str) -> bool:
        return True

    def crawl_delay(self, user_agent: str) -> float | None:
        return None


class DisallowAllRobotsPolicy:
    """Policy returned on 5xx errors, timeouts, or network failures (disallows all)."""

    def can_fetch(self, url: str, user_agent: str) -> bool:
        return False

    def crawl_delay(self, user_agent: str) -> float | None:
        return None


@dataclass(frozen=True, slots=True)
class RobotsCacheEntry:
    """Cached robots.txt policy for an origin (scheme://host:port)."""

    origin: str
    verdict: str  # "parsed" | "allow_all" | "disallow_all"
    policy: RobotsPolicy
    crawl_delay_seconds: float | None
    fetched_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class RobotsCache:
    """In-memory cache with optional SQLite persistence for robots policies."""

    def __init__(
        self,
        *,
        conn_provider: Callable[[], sqlite3.Connection] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._conn_provider = conn_provider
        self._clock = clock
        self._memory_cache: dict[str, RobotsCacheEntry] = {}

    def _policy_for_verdict(self, verdict: str, body: str | None) -> RobotsPolicy:
        if verdict == "allow_all":
            return AllowAllRobotsPolicy()
        if verdict == "disallow_all":
            return DisallowAllRobotsPolicy()
        return ProtegoRobotsPolicy(body or "")

    def get(self, origin: str) -> RobotsCacheEntry | None:
        """Retrieve a cached entry if present and unexpired."""
        now = self._clock()

        # Check in-memory first
        if origin in self._memory_cache:
            entry = self._memory_cache[origin]
            if not entry.is_expired(now):
                return entry
            del self._memory_cache[origin]

        # Check SQLite if available
        if self._conn_provider is not None:
            conn = self._conn_provider()
            row = conn.execute(
                "SELECT verdict, body, crawl_delay_seconds, fetched_at, expires_at "
                "FROM robots_cache WHERE origin = ?",
                (origin,),
            ).fetchone()
            if row is not None:
                expires_at = datetime.fromisoformat(row[4])
                if expires_at > now:
                    verdict = str(row[0])
                    body = row[1]
                    crawl_delay = float(row[2]) if row[2] is not None else None
                    fetched_at = datetime.fromisoformat(row[3])
                    policy = self._policy_for_verdict(verdict, body)
                    entry = RobotsCacheEntry(
                        origin=origin,
                        verdict=verdict,
                        policy=policy,
                        crawl_delay_seconds=crawl_delay,
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                    )
                    self._memory_cache[origin] = entry
                    return entry

        return None

    def put(
        self,
        origin: str,
        verdict: str,
        *,
        body: str | None = None,
        crawl_delay: float | None = None,
    ) -> RobotsCacheEntry:
        """Store a robots policy in cache with appropriate TTL."""
        now = self._clock()
        if verdict == "disallow_all":
            expires_at = now + DISALLOW_ALL_TTL
            policy: RobotsPolicy = DisallowAllRobotsPolicy()
        elif verdict == "allow_all":
            expires_at = now + ALLOW_ALL_TTL
            policy = AllowAllRobotsPolicy()
        else:
            expires_at = now + PARSED_TTL
            policy = ProtegoRobotsPolicy(body or "")
            if crawl_delay is None:
                # Extract crawl_delay from parsed policy if not explicitly passed
                crawl_delay = policy.crawl_delay("*")

        entry = RobotsCacheEntry(
            origin=origin,
            verdict=verdict,
            policy=policy,
            crawl_delay_seconds=crawl_delay,
            fetched_at=now,
            expires_at=expires_at,
        )
        self._memory_cache[origin] = entry

        if self._conn_provider is not None:
            conn = self._conn_provider()
            conn.execute(
                "INSERT OR REPLACE INTO robots_cache "
                "(origin, verdict, body, crawl_delay_seconds, fetched_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    origin,
                    verdict,
                    body,
                    crawl_delay,
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )

        return entry
