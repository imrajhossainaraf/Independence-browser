"""Canonical grouping, host diversity, and pagination over groups (Decision D7).

Rules implemented:
- Ingest candidate window (up to top 200 candidates)
- Group by canonical URL or exact content hash
- Keep best document per group as representative
- Host diversity cap: at most 2 results per host in top 10; extra results demoted, never dropped
- Deterministic group tie-breaking: score descending, canonical URL ascending
- Pagination over groups: page size default 10, max 20, max 100 visible results
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from search_engine.search.ranking import ScoreExplanation


@dataclass(frozen=True, slots=True)
class GroupedDocument:
    doc_id: int
    title: str
    url: str
    score: float
    doc_type: str
    lang: str
    description: str
    word_count: int
    published_at: str | None = None
    canonical_url: str | None = None
    canonical_group_id: int | None = None
    content_hash: str | None = None
    host: str = ""
    duplicate_count: int = 1
    score_explanation: ScoreExplanation | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "doc_id": self.doc_id,
            "title": self.title,
            "url": self.url,
            "score": round(self.score, 4),
            "doc_type": self.doc_type,
            "lang": self.lang,
            "description": self.description,
            "word_count": self.word_count,
            "published_at": self.published_at,
            "host": self.host,
            "duplicate_count": self.duplicate_count,
        }
        if self.canonical_group_id is not None:
            data["canonical_group_id"] = self.canonical_group_id
        if self.score_explanation:
            data["score_explanation"] = self.score_explanation.to_dict()
        return data


def extract_host(url: str) -> str:
    """Extract lowercase host from URL."""
    try:
        parsed = urlparse(url)
        return parsed.hostname.lower() if parsed.hostname else ""
    except ValueError:
        return ""


_INDEX_SUFFIXES = (
    "/index.html",
    "/index.htm",
    "/index.php",
    "/index.asp",
    "/default.html",
    "/default.htm",
)


def normalize_group_key(
    canonical_url: str | None,
    final_url: str,
    content_hash: str | None,
    canonical_group_id: int | None = None,
) -> str:
    """Derive canonical group key based on canonical_group_id, content hash, or canonical URL."""
    if canonical_group_id is not None:
        return f"group_id:{canonical_group_id}"
    if content_hash and content_hash.strip():
        return f"hash:{content_hash.strip().lower()}"
    target = (canonical_url or final_url).strip().lower()
    # Strip protocol and trailing slash for grouping
    if target.startswith("https://"):
        target = target[8:]
    elif target.startswith("http://"):
        target = target[7:]

    # Strip index file variants
    for suffix in _INDEX_SUFFIXES:
        if target.endswith(suffix):
            target = target[: -len(suffix)]
            break

    target = target.rstrip("/")
    return f"url:{target}"


def group_and_paginate(
    candidates: list[GroupedDocument],
    *,
    offset: int = 0,
    limit: int = 10,
    enable_host_diversity: bool = True,
) -> tuple[list[GroupedDocument], int]:
    """Group duplicate candidates, apply host diversity in top 10, and paginate over groups."""
    limit = max(1, min(limit, 20))
    offset = max(0, min(offset, 100))

    # 1. Group candidates by canonical key
    groups: dict[str, list[GroupedDocument]] = {}
    for doc in candidates:
        key = normalize_group_key(
            doc.canonical_url, doc.url, doc.content_hash, doc.canonical_group_id
        )
        groups.setdefault(key, []).append(doc)

    # 2. Pick best document per group as representative
    representatives: list[GroupedDocument] = []
    for _key, group_docs in groups.items():
        # Best by score DESC, then url ASC
        best = max(group_docs, key=lambda d: (d.score, -len(d.url)))
        dup_count = len(group_docs)
        host = best.host or extract_host(best.url)

        rep = GroupedDocument(
            doc_id=best.doc_id,
            title=best.title,
            url=best.url,
            score=best.score,
            doc_type=best.doc_type,
            lang=best.lang,
            description=best.description,
            word_count=best.word_count,
            published_at=best.published_at,
            canonical_url=best.canonical_url,
            canonical_group_id=best.canonical_group_id,
            content_hash=best.content_hash,
            host=host,
            duplicate_count=dup_count,
            score_explanation=best.score_explanation,
        )
        representatives.append(rep)

    # 3. Sort groups deterministically: score DESC, url ASC
    representatives.sort(key=lambda d: (-d.score, d.url))

    # 4. Host diversity re-ranking: at most 2 per host in top 10
    if enable_host_diversity and len(representatives) > 2:
        top_10: list[GroupedDocument] = []
        overflow: list[GroupedDocument] = []
        host_counts: dict[str, int] = {}

        for doc in representatives:
            h = doc.host
            current_h_count = host_counts.get(h, 0)
            if len(top_10) < 10 and current_h_count < 2:
                top_10.append(doc)
                host_counts[h] = current_h_count + 1
            else:
                overflow.append(doc)

        ranked_groups = top_10 + overflow
    else:
        ranked_groups = representatives

    total_groups = len(ranked_groups)

    # 5. Paginate over groups
    page_results = ranked_groups[offset : offset + limit]
    return page_results, total_groups
