"""Loaders for the human-edited input files. Bad lines are reported, never fatal.

Formats (one entry per line; blank lines and ``#`` comments are ignored):
  seeds.txt          one URL
  news_sources.txt   feed URL | name | category | poll minutes (opt.) | images yes/no (opt.)
  entities_seed.txt  one page title
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from search_engine.crawler.normalize import NormalizedUrl, normalize
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.errors import ConfigError
from search_engine.logs import redact_text

MIN_POLL_MINUTES = 15
MAX_ENTITY_TITLES = 2000
_CATEGORY = re.compile(r"[a-z0-9][a-z0-9-]{0,31}\Z")
_COMMENT = re.compile(r"\s+#")
_PREVIEW = 80


@dataclass(frozen=True, slots=True)
class LineProblem:
    line: int
    text: str
    reason: str


@dataclass(frozen=True, slots=True)
class NewsSource:
    feed: NormalizedUrl
    name: str
    category: str
    poll_interval_minutes: int
    images_allowed: bool


@dataclass
class LoadResult[T]:
    entries: list[T] = field(default_factory=list)
    problems: list[LineProblem] = field(default_factory=list)


def read_lines(path: Path) -> list[str]:
    """Read a text file as UTF-8 (a leading BOM is accepted). Invalid encoding is an error."""
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError as exc:
        raise ConfigError(f"input file not found: {path.name}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"input file is not valid UTF-8: {path.name}") from exc


def _clean(line: str) -> str:
    """Strip comments: a full-line '#' or a '#' preceded by whitespace (URL fragments stay)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return ""
    return _COMMENT.split(stripped, maxsplit=1)[0].strip()


def _problem(number: int, text: str, reason: str) -> LineProblem:
    return LineProblem(number, redact_text(text)[:_PREVIEW], reason)


def parse_seeds(lines: Iterable[str], *, allow_loopback: bool = False) -> LoadResult[NormalizedUrl]:
    result: LoadResult[NormalizedUrl] = LoadResult()
    seen: set[str] = set()
    for number, raw in enumerate(lines, start=1):
        text = _clean(raw)
        if not text:
            continue
        try:
            url = normalize(text, allow_loopback=allow_loopback)
        except UrlRejected as exc:
            result.problems.append(_problem(number, text, exc.code))
            continue
        if url.key in seen:
            result.problems.append(_problem(number, text, "duplicate"))
            continue
        seen.add(url.key)
        result.entries.append(url)
    return result


def parse_news_sources(
    lines: Iterable[str],
    *,
    default_interval_minutes: int = 30,
    allow_loopback: bool = False,
) -> LoadResult[NewsSource]:
    result: LoadResult[NewsSource] = LoadResult()
    seen: set[str] = set()
    for number, raw in enumerate(lines, start=1):
        text = _clean(raw)
        if not text:
            continue
        fields = [part.strip() for part in text.split("|")]
        if not 3 <= len(fields) <= 5:
            result.problems.append(_problem(number, text, "wrong_field_count"))
            continue
        url_text, name, category = fields[0], fields[1], fields[2].lower()
        interval_text = (
            fields[3] if len(fields) > 3 and fields[3] else str(default_interval_minutes)
        )
        images_text = fields[4].lower() if len(fields) > 4 and fields[4] else "no"
        try:
            feed = normalize(url_text, allow_loopback=allow_loopback)
        except UrlRejected as exc:
            result.problems.append(_problem(number, text, exc.code))
            continue
        reason = _news_field_problem(name, category, interval_text, images_text)
        if reason is not None:
            result.problems.append(_problem(number, text, reason))
            continue
        if feed.key in seen:
            result.problems.append(_problem(number, text, "duplicate"))
            continue
        seen.add(feed.key)
        result.entries.append(
            NewsSource(feed, name, category, int(interval_text), images_text == "yes")
        )
    return result


def _news_field_problem(name: str, category: str, interval: str, images: str) -> str | None:
    if not name or len(name) > 80 or any(unicodedata.category(c) == "Cc" for c in name):
        return "bad_name"
    if not _CATEGORY.match(category):
        return "bad_category"
    if not interval.isascii() or not interval.isdigit():
        return "bad_interval"
    if int(interval) < MIN_POLL_MINUTES:
        return "interval_too_small"
    if images not in {"yes", "no"}:
        return "bad_images_flag"
    return None


def parse_entity_seeds(
    lines: Iterable[str], *, max_entries: int = MAX_ENTITY_TITLES
) -> LoadResult[str]:
    result: LoadResult[str] = LoadResult()
    seen: set[str] = set()
    for number, raw in enumerate(lines, start=1):
        text = _clean(raw)
        if not text:
            continue
        if len(text) > 200 or any(unicodedata.category(c) == "Cc" for c in text):
            result.problems.append(_problem(number, text, "bad_title"))
            continue
        folded = unicodedata.normalize("NFKC", text).casefold()
        if folded in seen:
            result.problems.append(_problem(number, text, "duplicate"))
            continue
        if len(result.entries) >= max_entries:
            result.problems.append(_problem(number, text, "over_limit"))
            continue
        seen.add(folded)
        result.entries.append(text)
    return result


def build_allowlist(
    seeds: Iterable[NormalizedUrl], news: Iterable[NewsSource] = ()
) -> frozenset[str]:
    """Registered domains from the seed file and approved news sources (decision D12)."""
    domains = {url.registered_domain for url in seeds}
    domains.update(source.feed.registered_domain for source in news)
    return frozenset(domains)
