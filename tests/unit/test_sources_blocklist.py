from __future__ import annotations

from pathlib import Path

import pytest

from search_engine.crawler.blocklist import Blocklist
from search_engine.crawler.normalize import normalize
from search_engine.crawler.sources import (
    build_allowlist,
    parse_entity_seeds,
    parse_news_sources,
    parse_seeds,
    read_lines,
)
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.errors import ConfigError


def reasons(problems: list) -> list[str]:  # type: ignore[type-arg]
    return [p.reason for p in problems]


def test_seed_loader_handles_comments_blanks_duplicates_and_bad_lines() -> None:
    lines = [
        "# a comment",
        "",
        "   ",
        "https://example.com/start  # trailing comment",
        "https://Example.com:443/start#frag",  # duplicate of line 4 after normalization
        "https://example.org/page#section-not-a-comment",
        "ftp://example.net/",
        "https://localhost/",
        "not a url",
    ]
    result = parse_seeds(lines)
    assert [u.key for u in result.entries] == [
        "https://example.com/start",
        "https://example.org/page",
    ]
    assert [(p.line, p.reason) for p in result.problems] == [
        (5, "duplicate"),
        (7, "url_bad_scheme"),
        (8, "url_unsafe_host"),
        (9, "url_bad_scheme"),
    ]


def test_seed_loader_reports_never_raises_and_scrubs_secrets() -> None:
    result = parse_seeds(["https://user:topsecret@example.com/"])
    assert result.entries == []
    assert result.problems[0].reason == "url_credentials"
    assert len(result.problems[0].text) <= 80


def test_news_sources_valid_and_optional_fields() -> None:
    lines = [
        "https://news.example.com/feed.xml | Example News | World",
        "https://blog.example.org/rss | Example Blog | tech-blog | 60 | yes",
    ]
    result = parse_news_sources(lines, default_interval_minutes=30)
    assert result.problems == []
    first, second = result.entries
    assert (first.name, first.category, first.poll_interval_minutes, first.images_allowed) == (
        "Example News",
        "world",
        30,
        False,
    )
    assert second.poll_interval_minutes == 60 and second.images_allowed is True


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("https://a.example.com/feed | Only Name", "wrong_field_count"),
        ("a|b|c|d|e|f", "wrong_field_count"),
        ("ftp://a.example.com/feed | Name | world", "url_bad_scheme"),
        ("https://a.example.com/feed |  | world", "bad_name"),
        ("https://a.example.com/feed | Name | Bad Category!", "bad_category"),
        ("https://a.example.com/feed | Name | world | soon", "bad_interval"),
        ("https://a.example.com/feed | Name | world | 14", "interval_too_small"),
        ("https://a.example.com/feed | Name | world | 30 | maybe", "bad_images_flag"),
    ],
)
def test_news_sources_problems(line: str, reason: str) -> None:
    result = parse_news_sources([line])
    assert result.entries == []
    assert reasons(result.problems) == [reason]


def test_news_sources_duplicate_feed_reported() -> None:
    result = parse_news_sources(
        [
            "https://a.example.com/feed | A | world",
            "https://A.example.com:443/feed#x | A again | world",
        ]
    )
    assert len(result.entries) == 1
    assert reasons(result.problems) == ["duplicate"]


def test_entity_seeds() -> None:
    lines = ["Alan Turing", "# skip", "alan turing", "Marie Curie", "x" * 201, "bad\x00title"]
    result = parse_entity_seeds(lines)
    assert result.entries == ["Alan Turing", "Marie Curie"]
    assert reasons(result.problems) == ["duplicate", "bad_title", "bad_title"]


def test_entity_seed_cap() -> None:
    result = parse_entity_seeds([f"Title {n}" for n in range(5)], max_entries=3)
    assert len(result.entries) == 3
    assert reasons(result.problems) == ["over_limit", "over_limit"]


def test_allowlist_is_registered_domains_of_seeds_and_news() -> None:
    seeds = parse_seeds(["https://www.example.com/a", "https://docs.example.org/b"]).entries
    news = parse_news_sources(["https://feeds.example.net/rss | N | world"]).entries
    assert build_allowlist(seeds, news) == frozenset({"example.com", "example.org", "example.net"})
    assert build_allowlist(seeds) == frozenset({"example.com", "example.org"})


def test_read_lines_utf8_bom_and_errors(tmp_path: Path) -> None:
    good = tmp_path / "seeds.txt"
    good.write_bytes(b"\xef\xbb\xbfhttps://example.com/\n# c\n")
    assert parse_seeds(read_lines(good)).entries[0].key == "https://example.com/"
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(ConfigError, match="UTF-8"):
        read_lines(bad)
    with pytest.raises(ConfigError, match="not found"):
        read_lines(tmp_path / "missing.txt")


def test_blocklist_hosts_domains_and_prefixes() -> None:
    block = Blocklist()
    block.block_host("Ads.Example.com.")
    block.block_domain("tracker.net")
    block.block_prefix("https://example.com/private")
    assert block.reason(normalize("https://ads.example.com/x")) == "host"
    assert block.reason(normalize("https://sub.tracker.net/x")) == "domain"
    assert block.reason(normalize("https://example.com/private")) == "prefix"
    assert block.reason(normalize("https://example.com/private/deep?x=1")) == "prefix"
    assert block.reason(normalize("https://example.com/private?x=1")) == "prefix"
    assert block.reason(normalize("https://example.com/privateer")) is None
    assert block.reason(normalize("https://example.com/public")) is None
    assert not block.is_blocked(normalize("https://www.example.com/x"))
    assert block.is_blocked(normalize("https://ads.example.com/"))


def test_blocklist_trailing_slash_prefix_and_unblock() -> None:
    block = Blocklist()
    block.block_prefix("https://example.com/docs/")
    assert block.is_blocked(normalize("https://example.com/docs/a"))
    assert not block.is_blocked(normalize("https://example.com/docs"))
    block.unblock_prefix("https://example.com/docs/")
    assert not block.is_blocked(normalize("https://example.com/docs/a"))
    block.block_host("x.example.com")
    block.block_domain("bad.org")
    block.unblock_host("x.example.com")
    block.unblock_domain("bad.org")
    assert not block.hosts and not block.domains


def test_blocklist_rejects_invalid_prefix() -> None:
    with pytest.raises(UrlRejected):
        Blocklist().block_prefix("ftp://example.com/")
