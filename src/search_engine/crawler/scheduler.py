"""Host scheduler, politeness, and approval gateway (invariants I-1, I-4, I-7, decision D3).

Coordinates allowlist, blocklist, DNS resolution, IP policy, robots caching,
per-host crawl delay, per-host concurrency (1), global concurrency, and backoff with jitter.
Issues unforgeable single-use FetchApproval and RobotsFetchApproval tokens.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from search_engine.config import Settings
from search_engine.crawler.approvals import (
    FetchApproval,
    RobotsFetchApproval,
    issue_fetch_approval,
    issue_robots_approval,
)
from search_engine.crawler.blocklist import Blocklist
from search_engine.crawler.normalize import NormalizedUrl, normalize
from search_engine.crawler.robots import RobotsCache
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.net.errors import InvalidAddressError, ResolutionError
from search_engine.net.ip_policy import validate_resolved_ips
from search_engine.net.resolver import Resolver


@dataclass(frozen=True, slots=True)
class Denial:
    """Explains why the scheduler refused to issue a fetch approval."""

    reason: str
    retry_after_seconds: float | None = None


def _origin_for(norm: NormalizedUrl) -> str:
    port = norm.port or (443 if norm.scheme == "https" else 80)
    return f"{norm.scheme}://{norm.host}:{port}"


class Scheduler:
    """Schedules and approves all fetches according to safety, politeness, and concurrency."""

    def __init__(
        self,
        settings: Settings,
        resolver: Resolver,
        robots_cache: RobotsCache,
        *,
        allowlist: set[str] | None = None,
        blocklist: Blocklist | set[str] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        fetcher_ref: object = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.robots_cache = robots_cache
        self.allowlist = allowlist
        self.blocklist = blocklist
        self._clock = monotonic_clock
        self._rng = rng or random.Random(42)  # noqa: S311
        self._fetcher_ref = fetcher_ref

        # State tracking
        self.active_per_host: dict[str, int] = {}
        self.active_global: int = 0
        self.host_next_allowed: dict[str, float] = {}
        self.host_failures: dict[str, int] = {}

    def set_fetcher(self, fetcher: object) -> None:
        self._fetcher_ref = fetcher

    def _is_allowed_host(self, norm: NormalizedUrl) -> bool:
        if self.blocklist:
            if isinstance(self.blocklist, Blocklist):
                if self.blocklist.is_blocked(norm):
                    return False
            elif (norm.host in self.blocklist) or (norm.registered_domain in self.blocklist):
                return False
        if not self.settings.allowlist_mode:
            return True
        if not self.allowlist:
            return True
        return (norm.host in self.allowlist) or (norm.registered_domain in self.allowlist)

    def _resolve_and_validate_host(self, host: str) -> tuple[str, str | None]:
        """Resolve host and validate IP policy. Returns (pinned_ip, error_reason)."""
        try:
            ips = self.resolver.resolve(host, timeout=self.settings.dns_timeout_seconds)
        except ResolutionError as exc:
            return "", f"dns_failed: {exc.code}"

        try:
            valid_ips = validate_resolved_ips(
                ips, allow_loopback=self.settings.allow_loopback_for_tests
            )
        except InvalidAddressError as exc:
            return "", f"ip_rejected: {exc.code}"

        return str(valid_ips[0]), None

    def approve_robots(self, origin: str) -> RobotsFetchApproval | Denial:
        """Approve fetching a host's robots.txt.

        Applies URL safety, allowlist, DNS/IP policy, delay, and concurrency checks.
        Skips only the robots rule check itself.
        """
        parsed = urlsplit(origin)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        if not host:
            return Denial("invalid_origin")

        robots_url = f"{scheme}://{host}:{port}/robots.txt"
        try:
            norm = normalize(robots_url, allow_loopback=self.settings.allow_loopback_for_tests)
        except UrlRejected as exc:
            return Denial(f"url_rejected: {exc.code}")

        if not self._is_allowed_host(norm):
            return Denial("url_not_allowlisted")

        pinned_ip, err = self._resolve_and_validate_host(norm.host)
        if err:
            return Denial(err)

        return issue_robots_approval(
            norm.key,
            pinned_ip,
            host=norm.host,
            max_bytes=500 * 1024,
        )

    def _ensure_robots_loaded(self, origin: str, norm: NormalizedUrl) -> Denial | None:
        """Fetch and cache robots.txt if not already cached and fresh."""
        cached = self.robots_cache.get(origin)
        if cached is not None:
            return None

        # Fetch robots using SafeFetcher
        fetcher = getattr(self, "_fetcher_ref", None)
        if fetcher is None:
            # When fetcher is not wired yet, assume allow_all for tests
            self.robots_cache.put(origin, "allow_all")
            return None

        approval = self.approve_robots(origin)
        if isinstance(approval, Denial):
            # Robots request denied by safety/allowlist -> disallow all
            self.robots_cache.put(origin, "disallow_all")
            return None

        from search_engine.crawler.fetcher import FetchResult, Redirect

        # Drive up to 5 hops for robots redirects
        current_approval = approval
        for _ in range(6):
            res = fetcher.fetch_robots(current_approval)
            if isinstance(res, Redirect):
                # Verify target origin is allowlisted
                target_parsed = urlsplit(res.target)
                target_scheme = target_parsed.scheme.lower()
                target_host = target_parsed.hostname or ""
                target_port = target_parsed.port or (443 if target_scheme == "https" else 80)
                target_origin = f"{target_scheme}://{target_host}:{target_port}"

                next_approval = self.approve_robots(target_origin)
                if isinstance(next_approval, Denial):
                    self.robots_cache.put(origin, "disallow_all")
                    return None
                current_approval = next_approval
                continue

            if isinstance(res, FetchResult):
                if 200 <= res.status_code <= 299:
                    body_text = res.body.decode("utf-8", errors="replace")
                    self.robots_cache.put(origin, "parsed", body=body_text)
                    return None
                if 400 <= res.status_code <= 499:
                    self.robots_cache.put(origin, "allow_all")
                    return None
                # 5xx, timeouts, or network failures
                self.robots_cache.put(origin, "disallow_all")
                return None

        self.robots_cache.put(origin, "disallow_all")
        return None

    def approve(
        self,
        url: str,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        kind: str = "page",
        expected_content_types: tuple[str, ...] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchApproval | Denial:
        """Evaluate a URL and issue a FetchApproval if all safety, robots, and host rules pass."""
        # 1. Normalize URL
        try:
            norm = normalize(url, allow_loopback=self.settings.allow_loopback_for_tests)
        except UrlRejected as exc:
            return Denial(f"url_rejected: {exc.code}")

        # 2. Blocklist & Allowlist
        if not self._is_allowed_host(norm):
            return Denial("url_not_allowlisted")

        # 3. DNS resolution & IP policy
        pinned_ip, err = self._resolve_and_validate_host(norm.host)
        if err:
            return Denial(err)

        # 4. Robots check
        origin = _origin_for(norm)
        self._ensure_robots_loaded(origin, norm)
        robots_entry = self.robots_cache.get(origin)
        if robots_entry is not None:
            if not robots_entry.policy.can_fetch(norm.key, self.settings.user_agent_token):
                return Denial("robots_disallowed")
            if (
                robots_entry.crawl_delay_seconds is not None
                and robots_entry.crawl_delay_seconds > self.settings.max_crawl_delay_seconds
            ):
                return Denial("robots_crawl_delay_excessive")

        # 5. Politeness / Next allowed time check
        now = self._clock()
        next_allowed = self.host_next_allowed.get(norm.host, 0.0)
        if now < next_allowed:
            wait_s = next_allowed - now
            return Denial("politeness_delay", retry_after_seconds=wait_s)

        # 6. Concurrency limits
        active_host = self.active_per_host.get(norm.host, 0)
        if active_host >= self.settings.per_host_concurrency:
            return Denial("host_concurrency_limit")

        if self.active_global >= self.settings.global_concurrency:
            return Denial("global_concurrency_limit")

        # 7. Grant Approval
        self.active_per_host[norm.host] = active_host + 1
        self.active_global += 1

        return issue_fetch_approval(
            norm.key,
            pinned_ip,
            host=norm.host,
            max_bytes=max_bytes,
            expected_content_types=expected_content_types,
            kind=kind,
            etag=etag,
            last_modified=last_modified,
        )

    def release(
        self,
        approval: FetchApproval | RobotsFetchApproval,
        *,
        status_code: int = 200,
        retry: bool = False,
        elapsed_seconds: float = 0.0,
    ) -> None:
        """Release active concurrency and update host delay and exponential backoff."""
        host = approval.host
        if host in self.active_per_host:
            self.active_per_host[host] = max(0, self.active_per_host[host] - 1)
        self.active_global = max(0, self.active_global - 1)

        now = self._clock()

        # Check robots crawl delay
        entry = self.robots_cache.get(f"https://{host}:443") or self.robots_cache.get(
            f"http://{host}:80"
        )
        base_delay = (
            entry.crawl_delay_seconds
            if entry and entry.crawl_delay_seconds is not None
            else self.settings.per_host_delay_seconds
        )

        if retry or status_code == 429 or status_code >= 500 or status_code == 0:
            failures = self.host_failures.get(host, 0) + 1
            self.host_failures[host] = failures
            backoff = min(
                self.settings.backoff_base_seconds * (2 ** (failures - 1)),
                self.settings.backoff_max_seconds,
            )
            jitter = self._rng.uniform(0.0, 0.5 * backoff)
            delay = max(base_delay, backoff + jitter)
        else:
            self.host_failures[host] = 0
            delay = base_delay

        self.host_next_allowed[host] = now + delay
