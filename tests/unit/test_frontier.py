"""Unit tests for the SQLite-backed crawl frontier (Step 15, decision D8, D12)."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from search_engine.crawler.blocklist import Blocklist
from search_engine.crawler.frontier import (
    Frontier,
    FrontierConfig,
    calculate_priority,
)
from search_engine.db import Database


@pytest.fixture
def frontier_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "frontier.db").open()
    yield db
    db.close()


def test_priority_calculation() -> None:
    assert calculate_priority("seed", 0) == 100
    assert calculate_priority("redirect", 1) == 90
    assert calculate_priority("sitemap", 1) == 80
    assert calculate_priority("feed", 1) == 80
    assert calculate_priority("link", 0) == 50
    assert calculate_priority("link", 1) == 40
    assert calculate_priority("link", 2) == 30
    assert calculate_priority("link", 3) == 20
    assert calculate_priority("link", 4) == 10
    assert calculate_priority("link", 5) == 0
    assert calculate_priority("unknown", 0) == 0


def test_seeds_enter_the_queue(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(conn, FrontierConfig(allow_loopback=True))
        res = frontier.add_seed("https://example.com/start")
        assert res.status == "pending"
        assert res.created is True

        entry = frontier.get("https://example.com/start")
        assert entry is not None
        assert entry.url_key == "https://example.com/start"
        assert entry.status == "pending"
        assert entry.depth == 0
        assert entry.priority == 100
        assert entry.source == "seed"

        # Re-adding same seed does not duplicate
        res2 = frontier.add_seed("https://example.com/start")
        assert res2.created is False
        assert res2.status == "pending"
        assert frontier.stats()["pending"] == 1


def test_duplicates_merge_and_lower_depth(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(max_depth=3, allow_loopback=True, allowlist_mode=False),
        )
        seed_res = frontier.add_seed("https://example.com/seed")
        seed_id = seed_res.url_id

        # First discovered from a deep page at depth 3
        res1 = frontier.add_discovered_url(
            "https://example.com/page",
            source="link",
            parent_url_id=seed_id,
            depth=3,
        )
        assert res1.created is True
        assert res1.status == "pending"
        entry1 = frontier.get("https://example.com/page")
        assert entry1 is not None
        assert entry1.depth == 3
        assert entry1.priority == 20  # 50 - 3*10

        # Discovered again from a shorter path at depth 1
        res2 = frontier.add_discovered_url(
            "https://example.com/page",
            source="link",
            parent_url_id=seed_id,
            depth=1,
        )
        assert res2.created is False
        entry2 = frontier.get("https://example.com/page")
        assert entry2 is not None
        assert entry2.depth == 1
        assert entry2.priority == 40  # 50 - 1*10

        # Discovered from a longer path at depth 2 (should not increase depth)
        res3 = frontier.add_discovered_url(
            "https://example.com/page",
            source="link",
            parent_url_id=seed_id,
            depth=2,
        )
        assert res3.created is False
        entry3 = frontier.get("https://example.com/page")
        assert entry3 is not None
        assert entry3.depth == 1


def test_depth_exceeded_restored_when_discovered_at_lower_depth(
    frontier_db: Database,
) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(max_depth=2, allow_loopback=True, allowlist_mode=False),
        )
        # Discovered at depth 4 (exceeds max_depth 2)
        res1 = frontier.add_discovered_url("https://example.com/deep", depth=4)
        assert res1.status == "skipped"
        assert res1.reason == "depth_exceeded"

        entry1 = frontier.get("https://example.com/deep")
        assert entry1 is not None
        assert entry1.status == "skipped"

        # Discovered again at depth 1 (within max_depth) -> restored to pending
        res2 = frontier.add_discovered_url("https://example.com/deep", depth=1)
        assert res2.created is False
        entry2 = frontier.get("https://example.com/deep")
        assert entry2 is not None
        assert entry2.status == "pending"
        assert entry2.depth == 1
        assert entry2.priority == 40


def test_depth_limit_enforced(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(max_depth=2, allow_loopback=True, allowlist_mode=False),
        )
        res = frontier.add_discovered_url("https://example.com/too-deep", depth=3)
        assert res.status == "skipped"
        assert res.reason == "depth_exceeded"

        entry = frontier.get("https://example.com/too-deep")
        assert entry is not None
        assert entry.status == "skipped"
        assert entry.last_reason == "depth_exceeded"


def test_per_host_cap_enforced(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(max_pages_per_host=2, allow_loopback=True, allowlist_mode=False),
        )
        # Add 2 seeds and complete them
        u1 = frontier.add_seed("https://example.com/p1").url_id
        u2 = frontier.add_seed("https://example.com/p2").url_id
        frontier.complete(u1)
        frontier.complete(u2)

        # Host now has pages_stored = 2 (cap reached)
        res = frontier.add_discovered_url("https://example.com/p3", depth=1)
        assert res.status == "skipped"
        assert res.reason == "host_cap_reached"


def test_global_page_cap_enforced(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(max_pages=2, allow_loopback=True, allowlist_mode=False),
        )
        u1 = frontier.add_seed("https://a.com/p1").url_id
        u2 = frontier.add_seed("https://b.com/p2").url_id
        frontier.complete(u1)
        frontier.complete(u2)

        # Global cap 2 reached
        res = frontier.add_discovered_url("https://c.com/p3", depth=1)
        assert res.status == "skipped"
        assert res.reason == "global_cap_reached"

        # lease_next returns empty when global cap is reached
        frontier.add_seed("https://d.com/p4")  # Direct seed
        leased = frontier.lease_next(10)
        assert leased == []


def test_allowlist_scope_enforced(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(
                allowlist_mode=True,
                allowlist_domains={"example.com"},
                allow_loopback=True,
            ),
        )
        # Allowed domain
        res_allowed = frontier.add_discovered_url("https://sub.example.com/page", depth=1)
        assert res_allowed.status == "pending"

        # Disallowed domain
        res_disallowed = frontier.add_discovered_url("https://other.org/page", depth=1)
        assert res_disallowed.status == "skipped"
        assert res_disallowed.reason == "outside_allowlist"
        assert frontier.stats()["pending"] == 1
        assert frontier.stats()["skipped"] == 1


def test_blocklist_enforced(frontier_db: Database) -> None:
    blocklist = Blocklist()
    blocklist.block_host("bad.example.com")
    blocklist.block_domain("malicious.org")
    blocklist.block_prefix("https://example.com/private/")

    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(blocklist=blocklist, allow_loopback=True, allowlist_mode=False),
        )
        r1 = frontier.add_discovered_url("https://bad.example.com/test", depth=1)
        assert r1.status == "blocked"
        assert r1.reason == "blocked:host"

        r2 = frontier.add_discovered_url("https://sub.malicious.org/test", depth=1)
        assert r2.status == "blocked"
        assert r2.reason == "blocked:domain"

        r3 = frontier.add_discovered_url("https://example.com/private/secret", depth=1)
        assert r3.status == "blocked"
        assert r3.reason == "blocked:prefix"

        # None of the blocked URLs reached pending
        assert frontier.stats()["pending"] == 0
        assert frontier.stats()["blocked"] == 3


def test_nofollow_links_respected(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(allow_loopback=True, allowlist_mode=False),
        )
        parent_id = frontier.add_seed("https://example.com/").url_id

        res = frontier.add_discovered_url(
            "https://example.com/sponsored",
            parent_url_id=parent_id,
            nofollow=True,
        )
        assert res.status == "skipped"
        assert res.reason == "nofollow"

        # Link record was saved with nofollow=1
        links = frontier.get_links(parent_id)
        assert len(links) == 1
        assert links[0] == ("https://example.com/sponsored", True)


def test_leasing_priority_and_host_politeness(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(allow_loopback=True, allowlist_mode=False),
        )
        # Add URLs with different priorities and hosts
        frontier.add_discovered_url("https://host1.com/link", source="link", depth=2)  # priority 30
        frontier.add_discovered_url("https://host2.com/feed", source="feed", depth=1)  # priority 80
        frontier.add_seed("https://host1.com/seed")  # priority 100
        frontier.add_seed("https://host2.com/seed")  # priority 100

        # Batch size 2: Should pick host1 seed (100) and host2 seed (100), exactly one per host!
        leased = frontier.lease_next(batch_size=2)
        assert len(leased) == 2
        assert {u.host for u in leased} == {"host1.com", "host2.com"}
        assert all(u.priority == 100 for u in leased)

        # Active hosts constraint: if host2.com is active, only host1 can be leased
        leased2 = frontier.lease_next(batch_size=2, active_hosts={"host2.com"})
        assert len(leased2) == 1
        assert leased2[0].host == "host1.com"
        assert leased2[0].url_key == "https://host1.com/link"


def test_retry_transitions_and_exponential_backoff(frontier_db: Database) -> None:
    start_time = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(
                max_retries=3,
                backoff_base_seconds=2.0,
                backoff_max_seconds=10.0,
                allow_loopback=True,
                allowlist_mode=False,
            ),
        )
        url_id = frontier.add_seed("https://example.com/flaky", now=start_time).url_id
        leased = frontier.lease_next(1, now=start_time)
        assert len(leased) == 1

        # 1st failure -> retry 1 scheduled for start + 2s
        ok = frontier.retry(url_id, reason="503 timeout", now=start_time)
        assert ok is True
        e1 = frontier.get_by_id(url_id)
        assert e1 is not None
        assert e1.status == "retry"
        assert e1.retry_count == 1
        assert e1.next_attempt_at == (start_time + timedelta(seconds=2)).isoformat()

        # Before next_attempt_at: lease_next should NOT lease it
        assert frontier.lease_next(1, now=start_time + timedelta(seconds=1)) == []

        # After next_attempt_at: lease_next leases it
        leased_again = frontier.lease_next(1, now=start_time + timedelta(seconds=2))
        assert len(leased_again) == 1

        # 2nd failure -> retry 2 scheduled for 2 * 2^1 = 4s later
        now2 = start_time + timedelta(seconds=3)
        ok2 = frontier.retry(url_id, reason="503 timeout", now=now2)
        assert ok2 is True
        e2 = frontier.get_by_id(url_id)
        assert e2 is not None
        assert e2.retry_count == 2
        assert e2.next_attempt_at == (now2 + timedelta(seconds=4)).isoformat()

        # 3rd failure -> retry 3 scheduled for 2 * 2^2 = 8s later
        now3 = now2 + timedelta(seconds=5)
        ok3 = frontier.retry(url_id, reason="503 timeout", now=now3)
        assert ok3 is True
        e3 = frontier.get_by_id(url_id)
        assert e3 is not None
        assert e3.retry_count == 3
        assert e3.next_attempt_at == (now3 + timedelta(seconds=8)).isoformat()

        # 4th failure -> exceeds max_retries=3 -> failed_permanent
        now4 = now3 + timedelta(seconds=10)
        ok4 = frontier.retry(url_id, reason="503 timeout", now=now4)
        assert ok4 is False
        e4 = frontier.get_by_id(url_id)
        assert e4 is not None
        assert e4.status == "failed_permanent"
        assert e4.retry_count == 3


def test_crash_mid_lease_recovery_without_loss(frontier_db: Database) -> None:
    start_time = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(lease_timeout_seconds=60.0, allow_loopback=True, allowlist_mode=False),
        )
        frontier.add_seed("https://example.com/p1", now=start_time)
        frontier.add_seed("https://other.com/p2", now=start_time)

        # Worker leases both URLs
        leased = frontier.lease_next(2, now=start_time)
        assert len(leased) == 2
        assert frontier.stats()["leased"] == 2
        assert frontier.stats()["pending"] == 0

        # Before lease timeout: recovery returns 0
        assert frontier.recover_crashed_leases(now=start_time + timedelta(seconds=30)) == 0

        # Worker simulated crash: 61 seconds pass, lease has timed out
        crash_time = start_time + timedelta(seconds=61)
        recovered = frontier.recover_crashed_leases(now=crash_time)
        assert recovered == 2
        assert frontier.stats()["leased"] == 0
        assert frontier.stats()["pending"] == 2

        # Restarting worker leases them again without duplicate creation
        resumed = frontier.lease_next(2, now=crash_time)
        assert len(resumed) == 2
        assert frontier.stats()["total"] == 2


def test_reset_leases_on_cold_start(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(conn, FrontierConfig(allow_loopback=True, allowlist_mode=False))
        frontier.add_seed("https://example.com/p1")
        frontier.lease_next(1)
        assert frontier.stats()["leased"] == 1

        # Immediate crash / cold start reset
        reset_count = frontier.reset_leases()
        assert reset_count == 1
        assert frontier.stats()["leased"] == 0
        assert frontier.stats()["pending"] == 1


def test_sitemap_and_feed_discovery(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(
            conn,
            FrontierConfig(allow_loopback=True, allowlist_mode=False),
        )
        sitemap_results = frontier.add_sitemap_urls(
            ["https://example.com/s1", "https://example.com/s2"],
            depth=1,
        )
        assert len(sitemap_results) == 2
        assert all(r.status == "pending" for r in sitemap_results)

        feed_results = frontier.add_feed_urls(
            ["https://example.com/f1"],
            depth=1,
        )
        assert len(feed_results) == 1
        assert feed_results[0].status == "pending"

        s1_entry = frontier.get("https://example.com/s1")
        assert s1_entry is not None
        assert s1_entry.source == "sitemap"
        assert s1_entry.priority == 80

        f1_entry = frontier.get("https://example.com/f1")
        assert f1_entry is not None
        assert f1_entry.source == "feed"
        assert f1_entry.priority == 80


def test_malformed_discovered_urls_rejected(frontier_db: Database) -> None:
    with frontier_db.transaction() as conn:
        frontier = Frontier(conn, FrontierConfig())
        res = frontier.add_discovered_url("not a url at all")
        assert res.status == "rejected"
        assert res.created is False
        assert "rejected:" in (res.reason or "")
        assert frontier.stats()["total"] == 0
