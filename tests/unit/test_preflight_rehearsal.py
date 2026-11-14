"""Unit tests for Step 29 preflight preparation and operational controls."""

from __future__ import annotations

from pathlib import Path

import pytest

from search_engine.config import Settings
from search_engine.crawler.preflight import (
    build_manifest,
    compute_manifest_hash,
    create_preflight_approval,
    verify_preflight_approval,
)
from search_engine.crawler.sources import parse_news_sources, parse_seeds
from search_engine.errors import PolicyDenied
from search_engine.operations.controls import pause_crawler, resume_crawler, stop_crawler


def test_manifest_hashing_and_tamper_detection(tmp_path: Path) -> None:
    seeds_file = tmp_path / "seeds.txt"
    news_file = tmp_path / "news_sources.txt"
    entity_file = tmp_path / "entities_seed.txt"
    approved_file = tmp_path / "preflight.approved.json"

    seeds_file.write_text("https://example.com/\nhttps://docs.python.org/3/\n", encoding="utf-8")
    news_file.write_text(
        "https://feeds.bbci.co.uk/news/rss.xml | BBC | world | 30 | no\n", encoding="utf-8"
    )
    entity_file.write_text("Alan Turing\n", encoding="utf-8")

    settings = Settings(
        seeds_file=seeds_file,
        news_sources_file=news_file,
        entities_seed_file=entity_file,
        real_network_enabled=True,
        allow_loopback_for_tests=False,
    )

    manifest1 = build_manifest(settings)
    hash1 = compute_manifest_hash(manifest1)
    manifest2 = build_manifest(settings)
    hash2 = compute_manifest_hash(manifest2)
    assert hash1 == hash2

    # Create approval
    out_path = create_preflight_approval(settings, target_path=approved_file)
    assert out_path.exists()

    # Verification passes
    verify_preflight_approval(settings, preflight_path=approved_file)

    # Tamper with seeds file -> verification must fail
    seeds_file.write_text("https://example.com/\nhttps://malicious.org/\n", encoding="utf-8")
    with pytest.raises(PolicyDenied, match="does not match current inputs/settings"):
        verify_preflight_approval(settings, preflight_path=approved_file)


def test_real_network_refusal_when_approval_missing(tmp_path: Path) -> None:
    missing_file = tmp_path / "nonexistent_preflight.json"
    settings = Settings(
        real_network_enabled=True,
        allow_loopback_for_tests=False,
    )
    with pytest.raises(PolicyDenied, match="preflight approval file not found"):
        verify_preflight_approval(settings, preflight_path=missing_file)


def test_real_network_refusal_when_disabled() -> None:
    # When real network is disabled, verification raises PolicyDenied
    settings = Settings(
        real_network_enabled=False,
        allow_loopback_for_tests=False,
    )
    with pytest.raises(PolicyDenied, match="real network is disabled"):
        verify_preflight_approval(settings)


def test_stop_and_pause_control_file_rehearsal(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Initial state: neither stopped nor paused
    assert not (data_dir / "STOP").exists()
    assert not (data_dir / "PAUSE").exists()

    # Trigger pause
    pause_file = pause_crawler(data_dir)
    assert pause_file.exists()
    assert (data_dir / "PAUSE").exists()
    assert not (data_dir / "STOP").exists()

    # Resume from pause
    unpaused = resume_crawler(data_dir)
    assert unpaused
    assert not (data_dir / "PAUSE").exists()

    # Trigger emergency stop
    stop_file = stop_crawler(data_dir)
    assert stop_file.exists()
    assert (data_dir / "STOP").exists()


def test_news_sources_pipe_delimited_parsing() -> None:
    lines = [
        "# Header comment",
        "https://feeds.bbci.co.uk/news/rss.xml | BBC News | world | 30 | no",
        "https://news.ycombinator.com/rss | Hacker News | tech | 15 | no",
        "bad line with no delimiters",
        "https://example.com/feed | Example | science | 5 | no",  # invalid: interval < 15
    ]
    res = parse_news_sources(lines)
    assert len(res.entries) == 2
    assert res.entries[0].name == "BBC News"
    assert res.entries[0].category == "world"
    assert res.entries[0].poll_interval_minutes == 30
    assert not res.entries[0].images_allowed

    assert res.entries[1].name == "Hacker News"
    assert res.entries[1].category == "tech"
    assert res.entries[1].poll_interval_minutes == 15

    # 2 problematic lines reported cleanly without failing
    assert len(res.problems) == 2


def test_seeds_allowlist_derivation() -> None:
    lines = [
        "https://docs.python.org/3/library/index.html",
        "https://en.wikipedia.org/wiki/Computer_science",
        "https://docs.python.org/3/tutorial/",  # same domain as first
    ]
    res = parse_seeds(lines)
    assert len(res.entries) == 3
    domains = {u.registered_domain for u in res.entries}
    assert domains == {"python.org", "wikipedia.org"}
