from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from tests.fixtures.server import running_fixture_server

from search_engine.config import load_settings
from search_engine.crawler.approvals import FetchApproval, RobotsFetchApproval
from search_engine.crawler.fetcher import SafeFetcher
from search_engine.crawler.robots import (
    AllowAllRobotsPolicy,
    DisallowAllRobotsPolicy,
    ProtegoRobotsPolicy,
    RobotsCache,
)
from search_engine.crawler.scheduler import Denial, Scheduler
from search_engine.errors import InvalidApproval
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.resolver import FakeResolver

pytestmark = pytest.mark.loopback


# ==============================================================================
# 1. Robots Policy Tests (Protego adapter, wildcards, anchors, UA groups)
# ==============================================================================


def test_protego_policy_paths_wildcards_and_anchors() -> None:
    robots_text = """
User-agent: Googlebot
Disallow: /google-only/

User-agent: IndependentSearchBot
Disallow: /admin/
Allow: /admin/public$
Disallow: /secret/*private
Crawl-delay: 5

User-agent: *
Disallow: /blocked/
"""
    policy = ProtegoRobotsPolicy(robots_text)
    ua = "IndependentSearchBot"

    # Admin subpaths disallowed except exact /admin/public
    assert not policy.can_fetch("http://example.com/admin/dashboard", ua)
    assert not policy.can_fetch("http://example.com/admin/public/more", ua)
    assert policy.can_fetch("http://example.com/admin/public", ua)

    # Wildcards
    assert not policy.can_fetch("http://example.com/secret/a/b/private", ua)
    assert policy.can_fetch("http://example.com/secret/a/b/public", ua)

    # Other bot groups
    assert policy.can_fetch("http://example.com/google-only/test", ua)
    assert not policy.can_fetch("http://example.com/blocked/test", "*")

    # Crawl delay
    assert policy.crawl_delay(ua) == 5.0
    assert policy.crawl_delay("Googlebot") is None


def test_allow_all_and_disallow_all_policies() -> None:
    allow = AllowAllRobotsPolicy()
    assert allow.can_fetch("http://example.com/any", "bot")
    assert allow.crawl_delay("bot") is None

    disallow = DisallowAllRobotsPolicy()
    assert not disallow.can_fetch("http://example.com/any", "bot")
    assert disallow.crawl_delay("bot") is None


# ==============================================================================
# 2. Robots Cache Tests (TTL, Hit, Expiry, Verdicts)
# ==============================================================================


def test_robots_cache_ttl_and_expiry() -> None:
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    current_time = base_time

    def fake_clock() -> datetime:
        return current_time

    cache = RobotsCache(clock=fake_clock)

    # Parsed cache entry TTL: 24h
    cache.put("http://example.com:80", "parsed", body="User-agent: *\nAllow: /\n")
    entry = cache.get("http://example.com:80")
    assert entry is not None
    assert entry.verdict == "parsed"
    assert entry.expires_at == base_time + timedelta(hours=24)

    # Advance 23h -> still fresh
    current_time = base_time + timedelta(hours=23)
    assert cache.get("http://example.com:80") is not None

    # Advance 25h -> expired
    current_time = base_time + timedelta(hours=25)
    assert cache.get("http://example.com:80") is None

    # Disallow_all failure TTL: 10m
    cache.put("http://fail.com:80", "disallow_all")
    fail_entry = cache.get("http://fail.com:80")
    assert fail_entry is not None
    assert fail_entry.expires_at == current_time + timedelta(minutes=10)

    # Advance 11m -> expired
    current_time = current_time + timedelta(minutes=11)
    assert cache.get("http://fail.com:80") is None


def test_robots_cache_sqlite_persistence(tmp_path: object) -> None:
    import sqlite3
    from pathlib import Path

    p = Path(str(tmp_path)) / "test.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE robots_cache (origin TEXT PRIMARY KEY, verdict TEXT, body TEXT, "
        "crawl_delay_seconds REAL, fetched_at TEXT, expires_at TEXT)"
    )

    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    cache1 = RobotsCache(conn_provider=lambda: conn, clock=lambda: base_time)
    cache1.put("http://example.com:80", "parsed", body="User-agent: *\nAllow: /\n", crawl_delay=3.0)
    cache1.put("http://allow.com:80", "allow_all")
    cache1.put("http://disallow.com:80", "disallow_all")

    # Read back from second cache instance with same DB
    cache2 = RobotsCache(conn_provider=lambda: conn, clock=lambda: base_time)
    e1 = cache2.get("http://example.com:80")
    assert e1 is not None and e1.verdict == "parsed" and e1.crawl_delay_seconds == 3.0
    assert e1.policy.can_fetch("http://example.com/test", "*")

    e2 = cache2.get("http://allow.com:80")
    assert e2 is not None and e2.verdict == "allow_all"
    assert e2.policy.can_fetch("http://allow.com/test", "*")

    e3 = cache2.get("http://disallow.com:80")
    assert e3 is not None and e3.verdict == "disallow_all"
    assert not e3.policy.can_fetch("http://disallow.com/test", "*")
    conn.close()


# ==============================================================================
# 3. Scheduler Approval & Politeness Tests
# ==============================================================================


def test_scheduler_approval_order() -> None:
    settings = load_settings(overrides={"allow_loopback_for_tests": True, "allowlist_mode": True})
    resolver = FakeResolver(table={"example.com": ["127.0.0.1"], "badip.com": ["10.0.0.1"]})
    cache = RobotsCache()
    scheduler = Scheduler(
        settings,
        resolver,
        cache,
        allowlist={"example.com"},
        blocklist={"blocked.com"},
    )

    # 1. URL rejected (syntax)
    res_syntax = scheduler.approve("not-a-valid-url")
    assert isinstance(res_syntax, Denial)
    assert res_syntax.reason.startswith("url_rejected")

    # 2. Blocklisted
    res_block = scheduler.approve("http://blocked.com/")
    assert isinstance(res_block, Denial)
    assert res_block.reason == "url_not_allowlisted"

    # 3. Not in allowlist
    res_allow = scheduler.approve("http://other.com/")
    assert isinstance(res_allow, Denial)
    assert res_allow.reason == "url_not_allowlisted"

    # 4. IP policy rejected
    assert scheduler.allowlist is not None
    scheduler.allowlist.add("badip.com")
    res_ip = scheduler.approve("http://badip.com/")
    assert isinstance(res_ip, Denial)
    assert res_ip.reason.startswith("ip_rejected")

    # 5. Success
    app = scheduler.approve("http://example.com/page")
    assert isinstance(app, FetchApproval)
    assert app.url == "http://example.com/page"
    assert app.pinned_ip == "127.0.0.1"


def test_scheduler_robots_disallowed_and_delay_excessive() -> None:
    settings = load_settings(
        overrides={"allow_loopback_for_tests": True, "max_crawl_delay_seconds": 10.0}
    )
    resolver = FakeResolver(table={"example.com": ["127.0.0.1"]})
    cache = RobotsCache()
    scheduler = Scheduler(settings, resolver, cache, allowlist={"example.com"})

    # Put a disallow rule
    cache.put("http://example.com:80", "parsed", body="User-agent: *\nDisallow: /forbidden\n")
    denial = scheduler.approve("http://example.com/forbidden")
    assert isinstance(denial, Denial)
    assert denial.reason == "robots_disallowed"

    # Put an excessive crawl delay
    cache.put(
        "http://example.com:80",
        "parsed",
        body="User-agent: *\nCrawl-delay: 30\n",
        crawl_delay=30.0,
    )
    denial_delay = scheduler.approve("http://example.com/allowed")
    assert isinstance(denial_delay, Denial)
    assert denial_delay.reason == "robots_crawl_delay_excessive"


def test_scheduler_concurrency_and_delays() -> None:
    settings = load_settings(
        overrides={
            "allow_loopback_for_tests": True,
            "per_host_concurrency": 1,
            "global_concurrency": 2,
            "per_host_delay_seconds": 5.0,
        }
    )
    resolver = FakeResolver(
        table={"a.com": ["127.0.0.1"], "b.com": ["127.0.0.1"], "c.com": ["127.0.0.1"]}
    )
    cache = RobotsCache()
    current_time = 100.0

    scheduler = Scheduler(
        settings,
        resolver,
        cache,
        allowlist={"a.com", "b.com", "c.com"},
        monotonic_clock=lambda: current_time,
    )

    # 1. First request on a.com approved
    app_a1 = scheduler.approve("http://a.com/1")
    assert isinstance(app_a1, FetchApproval)

    # 2. Second concurrent request on a.com denied by per-host concurrency (1)
    denial_host = scheduler.approve("http://a.com/2")
    assert isinstance(denial_host, Denial)
    assert denial_host.reason == "host_concurrency_limit"

    # 3. Request on b.com approved (global count reaches 2)
    app_b = scheduler.approve("http://b.com/1")
    assert isinstance(app_b, FetchApproval)

    # 4. Third request on c.com denied by global concurrency cap (2)
    denial_global = scheduler.approve("http://c.com/1")
    assert isinstance(denial_global, Denial)
    assert denial_global.reason == "global_concurrency_limit"

    # 5. Release a.com
    scheduler.release(app_a1, status_code=200)

    # Now a.com is subject to politeness delay
    denial_delay = scheduler.approve("http://a.com/2")
    assert isinstance(denial_delay, Denial)
    assert denial_delay.reason == "politeness_delay"
    assert denial_delay.retry_after_seconds == 5.0

    # Advance clock past delay
    current_time += 5.1
    app_a2 = scheduler.approve("http://a.com/2")
    assert isinstance(app_a2, FetchApproval)


def test_scheduler_exponential_backoff_growth_and_cap() -> None:
    settings = load_settings(
        overrides={
            "allow_loopback_for_tests": True,
            "backoff_base_seconds": 2.0,
            "backoff_max_seconds": 16.0,
        }
    )
    resolver = FakeResolver(table={"example.com": ["127.0.0.1"]})
    cache = RobotsCache()
    current_time = 0.0

    rng = random.Random(123)  # noqa: S311
    scheduler = Scheduler(
        settings,
        resolver,
        cache,
        allowlist={"example.com"},
        monotonic_clock=lambda: current_time,
        rng=rng,
    )

    app = scheduler.approve("http://example.com/1")
    assert isinstance(app, FetchApproval)

    # Failure 1: base ~ 2s
    scheduler.release(app, status_code=500, retry=True)
    next_time_1 = scheduler.host_next_allowed["example.com"]
    assert 2.0 <= next_time_1 <= 3.0

    # Advance clock and fail again
    current_time = next_time_1 + 1.0
    app2 = scheduler.approve("http://example.com/2")
    assert isinstance(app2, FetchApproval)

    # Failure 2: base 4s
    scheduler.release(app2, status_code=500, retry=True)
    next_time_2 = scheduler.host_next_allowed["example.com"] - current_time
    assert 4.0 <= next_time_2 <= 6.0

    # Advance clock past second failure delay and simulate many failures to verify ceiling
    current_time = scheduler.host_next_allowed["example.com"] + 1.0
    scheduler.host_failures["example.com"] = 10
    app3 = scheduler.approve("http://example.com/3")
    assert isinstance(app3, FetchApproval)
    scheduler.release(app3, status_code=500, retry=True)
    next_time_capped = scheduler.host_next_allowed["example.com"] - current_time
    assert next_time_capped <= 24.0  # max 16 + max 8 jitter


# ==============================================================================
# 4. End-to-End Robots Loading via SafeFetcher & Fixture Server
# ==============================================================================


def test_scheduler_loads_robots_from_fixture_server() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(
            overrides={"allow_loopback_for_tests": True, "allowlist_mode": True}
        )
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        resolver = FakeResolver(table={server_info.host: ["127.0.0.1"]})
        cache = RobotsCache()

        # Set robots to disallow /forbidden
        server_info.server.robots_content = "User-agent: *\nDisallow: /forbidden\n"

        scheduler = Scheduler(
            settings,
            resolver,
            cache,
            allowlist={server_info.host},
            fetcher_ref=fetcher,
        )

        # 1. Allowed URL succeeds
        app_ok = scheduler.approve(server_info.url_for("/normal"))
        assert isinstance(app_ok, FetchApproval)

        # Cache entry should now be 'parsed'
        origin = f"http://{server_info.host}:{server_info.port}"
        cached = cache.get(origin)
        assert cached is not None
        assert cached.verdict == "parsed"

        # 2. Forbidden URL denied
        scheduler.release(app_ok)
        app_bad = scheduler.approve(server_info.url_for("/forbidden"))
        assert isinstance(app_bad, Denial)
        assert app_bad.reason == "robots_disallowed"


def test_scheduler_refused_robots_redirect_disallows_all() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(
            overrides={"allow_loopback_for_tests": True, "allowlist_mode": True}
        )
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        resolver = FakeResolver(table={server_info.host: ["127.0.0.1"]})
        cache = RobotsCache()

        # Robots redirects to an unallowlisted host
        server_info.server.register_handler(
            "/robots.txt",
            lambda h: h._send_redirect("http://forbidden-host.example/robots.txt"),
        )

        scheduler = Scheduler(
            settings,
            resolver,
            cache,
            allowlist={server_info.host},  # forbidden-host.example is NOT allowlisted
            fetcher_ref=fetcher,
        )

        origin = f"http://{server_info.host}:{server_info.port}"
        # Approve normal URL -> robots fetch tries to follow redirect to forbidden host
        res = scheduler.approve(server_info.url_for("/normal"))
        assert isinstance(res, Denial)
        assert res.reason == "robots_disallowed"

        # Cached verdict should be 'disallow_all'
        cached = cache.get(origin)
        assert cached is not None
        assert cached.verdict == "disallow_all"


def test_scheduler_robots_404_allows_and_500_disallows() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(
            overrides={"allow_loopback_for_tests": True, "allowlist_mode": True}
        )
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)
        resolver = FakeResolver(table={server_info.host: ["127.0.0.1"]})

        # 1. 404 robots -> allow all
        cache_404 = RobotsCache()
        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_response_simple(404, b"", "text/plain")
        )
        s_404 = Scheduler(
            settings, resolver, cache_404, allowlist={server_info.host}, fetcher_ref=fetcher
        )
        app_404 = s_404.approve(server_info.url_for("/normal"))
        assert isinstance(app_404, FetchApproval)
        origin = f"http://{server_info.host}:{server_info.port}"
        assert cache_404.get(origin).verdict == "allow_all"  # type: ignore[union-attr]

        # 2. 500 robots -> disallow all
        cache_500 = RobotsCache()
        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_response_simple(500, b"", "text/plain")
        )
        s_500 = Scheduler(
            settings, resolver, cache_500, allowlist={server_info.host}, fetcher_ref=fetcher
        )
        denial_500 = s_500.approve(server_info.url_for("/normal"))
        assert isinstance(denial_500, Denial)
        assert denial_500.reason == "robots_disallowed"
        assert cache_500.get(origin).verdict == "disallow_all"  # type: ignore[union-attr]


def test_scheduler_approve_robots_invalid_origin() -> None:
    settings = load_settings(overrides={"allow_loopback_for_tests": True})
    scheduler = Scheduler(settings, FakeResolver(), RobotsCache())
    res = scheduler.approve_robots("http://")
    assert isinstance(res, Denial)
    assert res.reason == "invalid_origin"


# ==============================================================================
# 5. Invariant Gate Tests
# ==============================================================================


def test_gate_fetcher_cannot_be_called_without_valid_approval() -> None:
    settings = load_settings(overrides={"allow_loopback_for_tests": True})
    transport = HttpxTransport(allow_loopback=True)
    fetcher = SafeFetcher(transport, settings)

    # 1. Passing raw URL or forged token fails
    with pytest.raises(InvalidApproval):
        fetcher.fetch_once("http://example.com/")  # type: ignore[arg-type]

    # 2. Passing fake token without sentinel fails
    with pytest.raises(InvalidApproval):
        fetcher.fetch_once(
            FetchApproval(url="http://example.com/", pinned_ip="127.0.0.1", host="example.com")
        )

    # 3. Passing RobotsFetchApproval to fetch_once fails
    with pytest.raises(InvalidApproval):
        fetcher.fetch_once(
            RobotsFetchApproval(  # type: ignore[arg-type]
                url="http://example.com/robots.txt", pinned_ip="127.0.0.1", host="example.com"
            )
        )
