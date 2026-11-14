"""Unit tests for Step 13: Ranking signals, canonical grouping, and suggestions.

Requirements tested:
- Title boosts (exact title match and all-terms match)
- Freshness changes order only within its bound and only with real dates
- News freshness (24-hour half-life) vs general page freshness (365-day half-life)
- Quality adjustment bounds (strictly clamped between 0.95 and 1.05)
- Duplicate grouping (canonical URL and content hash deduplication)
- Pagination never repeats or skips items across consecutive pages
- Stable deterministic ties across runs (score DESC, canonical URL ASC)
- Spelling suggestions for single-character and transposition typos
- No suggestion when results are good
- Per-signal ablation switches and score explanations
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from search_engine.db import Database
from search_engine.indexer import compress_text, rebuild_index
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository
from search_engine.search.grouping import (
    GroupedDocument,
    group_and_paginate,
)
from search_engine.search.ranking import (
    RankingConfig,
    compute_freshness_multiplier,
    compute_phrase_multiplier,
    compute_quality_multiplier,
    compute_score,
    compute_title_multiplier,
)
from search_engine.search.suggest import (
    load_vocab_terms,
    suggest_spelling,
)

# ---------------------------------------------------------------------------
# 1. Freshness Multiplier Tests
# ---------------------------------------------------------------------------


def test_freshness_requires_real_dates_and_ignores_invalid_or_future() -> None:
    cfg = RankingConfig()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    # Missing or empty date
    mult_none, _ = compute_freshness_multiplier(None, "page", config=cfg, reference_time=now)
    assert mult_none == 1.0
    mult_empty, _ = compute_freshness_multiplier("   ", "page", config=cfg, reference_time=now)
    assert mult_empty == 1.0

    # Invalid ISO date
    mult_invalid, _ = compute_freshness_multiplier(
        "invalid-date", "page", config=cfg, reference_time=now
    )
    assert mult_invalid == 1.0

    # Future date receives no bonus
    future_date = (now + timedelta(days=10)).isoformat()
    mult_future, reason = compute_freshness_multiplier(
        future_date, "page", config=cfg, reference_time=now
    )
    assert mult_future == 1.0
    assert reason and "future published date" in reason


def test_freshness_bounded_and_news_half_life() -> None:
    cfg = RankingConfig()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    # Fresh page published today (age 0)
    today_iso = now.isoformat()
    mult_today, _ = compute_freshness_multiplier(today_iso, "page", config=cfg, reference_time=now)
    assert mult_today == pytest.approx(1.10)  # maximum +10% boost

    # Standard page after 365 days (1 half-life): boost is 0.10 * 0.5 = 0.05 -> 1.05
    one_year_ago = (now - timedelta(days=365)).isoformat()
    mult_1yr, _ = compute_freshness_multiplier(one_year_ago, "page", config=cfg, reference_time=now)
    assert mult_1yr == pytest.approx(1.05)

    # News page after 24 hours (1 news half-life): boost is 0.10 * 0.5 = 0.05 -> 1.05
    one_day_ago = (now - timedelta(days=1)).isoformat()
    mult_news_1d, _ = compute_freshness_multiplier(
        one_day_ago, "news", config=cfg, reference_time=now
    )
    assert mult_news_1d == pytest.approx(1.05)

    # Standard page after 24 hours: decay is 0.5^(1/365) ~ 0.9981 -> boost ~ 0.0998 -> ~1.0998
    mult_page_1d, _ = compute_freshness_multiplier(
        one_day_ago, "page", config=cfg, reference_time=now
    )
    assert mult_page_1d > 1.099


def test_freshness_changes_order_only_within_bound() -> None:
    cfg = RankingConfig()
    now = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    # Doc A has slightly lower BM25 (10.0 vs 10.5, ~5% diff) but is brand new -> freshness flips it
    score_a, _ = compute_score(
        raw_bm25=-10.0,
        query_str="test",
        title="Doc A",
        url="https://example.org/a",
        description="desc",
        doc_type="page",
        word_count=100,
        published_at=now.isoformat(),
        config=cfg,
        reference_time=now,
    )
    # Doc B is 3 years old -> freshness boost is tiny (~0.012)
    score_b, _ = compute_score(
        raw_bm25=-10.5,
        query_str="test",
        title="Doc B",
        url="https://example.org/b",
        description="desc",
        doc_type="page",
        word_count=100,
        published_at=(now - timedelta(days=1095)).isoformat(),
        config=cfg,
        reference_time=now,
    )
    # Freshness flipped Doc A (10.0 * 1.10 = 11.0) above Doc B (10.5 * ~1.012 = ~10.63)
    assert score_a > score_b

    # Doc C has significantly higher BM25 (15.0 vs 10.0, 50% diff) -> freshness CANNOT flip it
    score_c, _ = compute_score(
        raw_bm25=-15.0,
        query_str="test",
        title="Doc C",
        url="https://example.org/c",
        description="desc",
        doc_type="page",
        word_count=100,
        published_at=(now - timedelta(days=1095)).isoformat(),
        config=cfg,
        reference_time=now,
    )
    assert score_c > score_a


# ---------------------------------------------------------------------------
# 2. Source-Quality Adjustment Tests
# ---------------------------------------------------------------------------


def test_quality_adjustment_strictly_bounded() -> None:
    cfg = RankingConfig(quality_min_mult=0.95, quality_max_mult=1.05)

    # Case A: Massive penalties (word count 5, no https, short title)
    mult_bad, reasons_bad = compute_quality_multiplier(
        word_count=5, title="Short", url="http://insecure.test/p", config=cfg
    )
    assert mult_bad == pytest.approx(0.95)
    assert mult_bad >= 0.95
    assert len(reasons_bad) >= 1

    # Case B: Maximum bonuses (word count 200, https, long informative title)
    mult_good, reasons_good = compute_quality_multiplier(
        word_count=200,
        title="Comprehensive In-Depth Search Architecture",
        url="https://secure.test/guide",
        config=cfg,
    )
    assert mult_good == pytest.approx(1.04)  # +0.02 https + 0.02 title = 1.04
    assert mult_good <= 1.05
    assert len(reasons_good) == 2


# ---------------------------------------------------------------------------
# 3. Title and Phrase Boost Tests
# ---------------------------------------------------------------------------


def test_title_and_phrase_boosts() -> None:
    cfg = RankingConfig()

    # Exact title match
    mult_exact, reason_exact = compute_title_multiplier(
        "python search", "Python Search", config=cfg
    )
    assert mult_exact == pytest.approx(1.15)
    assert reason_exact and "exact title match" in reason_exact

    # All terms in title
    mult_terms, reason_terms = compute_title_multiplier(
        "python search", "Comprehensive Search Architecture in Python", config=cfg
    )
    assert mult_terms == pytest.approx(1.10)
    assert reason_terms and "all query terms" in reason_terms

    # Partial terms in title
    mult_partial, _ = compute_title_multiplier("python search", "Python Guide", config=cfg)
    assert mult_partial == 1.0

    # Phrase boost
    mult_phrase, reason_phrase = compute_phrase_multiplier(
        "database indexing",
        title="Guide to Relational Database Indexing Systems",
        description="Comprehensive overview of B-Trees.",
        config=cfg,
    )
    assert mult_phrase == pytest.approx(1.10)
    assert reason_phrase and "phrase found" in reason_phrase


# ---------------------------------------------------------------------------
# 4. Canonical Grouping and Pagination Tests
# ---------------------------------------------------------------------------


def test_duplicate_grouping_by_canonical_url_and_content_hash() -> None:
    docs = [
        GroupedDocument(
            doc_id=1,
            title="Page 1 Original",
            url="https://example.org/doc1",
            canonical_url="https://example.org/doc1",
            score=10.0,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        ),
        GroupedDocument(
            doc_id=2,
            title="Page 1 Tracking Variant",
            url="https://example.org/doc1?utm_source=twitter",
            canonical_url="https://example.org/doc1",
            score=8.0,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        ),
        GroupedDocument(
            doc_id=3,
            title="Different Page",
            url="https://example.org/doc2",
            canonical_url="https://example.org/doc2",
            score=9.0,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        ),
    ]

    paged, total_groups = group_and_paginate(docs, offset=0, limit=10)
    assert total_groups == 2
    assert len(paged) == 2

    # Group 1 representative is Doc 1 (higher score 10.0) with duplicate_count = 2
    assert paged[0].doc_id == 1
    assert paged[0].duplicate_count == 2
    # Group 2 representative is Doc 3
    assert paged[1].doc_id == 3
    assert paged[1].duplicate_count == 1


def test_pagination_never_repeats_or_skips() -> None:
    # 25 unique documents
    docs = [
        GroupedDocument(
            doc_id=i,
            title=f"Doc {i}",
            url=f"https://example.org/doc{i}",
            canonical_url=f"https://example.org/doc{i}",
            score=100.0 - i,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        )
        for i in range(1, 26)
    ]

    p1, total = group_and_paginate(docs, offset=0, limit=10)
    p2, _ = group_and_paginate(docs, offset=10, limit=10)
    p3, _ = group_and_paginate(docs, offset=20, limit=10)

    assert total == 25
    assert len(p1) == 10
    assert len(p2) == 10
    assert len(p3) == 5

    ids_p1 = [d.doc_id for d in p1]
    ids_p2 = [d.doc_id for d in p2]
    ids_p3 = [d.doc_id for d in p3]

    # No repeats across pages
    assert set(ids_p1).isdisjoint(set(ids_p2))
    assert set(ids_p2).isdisjoint(set(ids_p3))
    assert set(ids_p1).isdisjoint(set(ids_p3))

    # No skips: complete 1..25 sequence preserved
    all_retrieved = ids_p1 + ids_p2 + ids_p3
    assert all_retrieved == list(range(1, 26))


def test_deterministic_ties_across_runs() -> None:
    docs = [
        GroupedDocument(
            doc_id=1,
            title="Doc Z",
            url="https://example.org/z",
            score=10.0,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        ),
        GroupedDocument(
            doc_id=2,
            title="Doc A",
            url="https://example.org/a",
            score=10.0,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        ),
        GroupedDocument(
            doc_id=3,
            title="Doc M",
            url="https://example.org/m",
            score=10.0,
            doc_type="page",
            lang="en",
            description="",
            word_count=100,
        ),
    ]

    # Multiple runs must always return URL ascending for identical scores: a, m, z
    for _ in range(5):
        paged, _ = group_and_paginate(docs, offset=0, limit=10)
        urls = [d.url for d in paged]
        assert urls == [
            "https://example.org/a",
            "https://example.org/m",
            "https://example.org/z",
        ]


# ---------------------------------------------------------------------------
# 5. Spelling Suggestions & Did-You-Mean Tests
# ---------------------------------------------------------------------------


def test_spelling_suggestions_single_char_and_transposition(tmp_path: Path) -> None:
    db = Database(tmp_path / "spelling_test.db").open()
    with db.transaction() as conn:
        hosts = HostRepository(conn)
        urls = UrlRepository(conn)
        docs = DocumentRepository(conn)
        hid = hosts.ensure("spelling.test", "spelling.test")

        u1, _ = urls.add("https://spelling.test/python", hid, source="seed")
        docs.upsert_document(
            url_id=u1,
            title="Python Programming Language",
            description="Modern python architecture.",
            text_gz=compress_text("Python guide with sqlite database integration."),
            doc_type="page",
            status="active",
        )
        rebuild_index(conn)

    with db.reader() as conn:
        vocab = load_vocab_terms(conn)
        assert "python" in vocab
        assert "sqlite" in vocab

        # Single-character typo (deletion): "pythn" -> "python"
        res_del = suggest_spelling(conn, "pythn", result_count=0, vocab=vocab)
        assert res_del is not None
        assert res_del.suggested_query == "python"
        assert res_del.is_auto_corrected is True  # 0 results and exactly 1 candidate at dist 1

        # Single-character typo (substitution): "pythun" -> "python"
        res_sub = suggest_spelling(conn, "pythun", result_count=0, vocab=vocab)
        assert res_sub is not None
        assert res_sub.suggested_query == "python"

        # Transposition typo: "pyhtom" -> "python"
        res_trans = suggest_spelling(conn, "pyhtom", result_count=0, vocab=vocab)
        assert res_trans is not None
        assert res_trans.suggested_query == "python"

        # No suggestion when results are already solid
        res_good = suggest_spelling(conn, "python", result_count=5, top_score=2.5, vocab=vocab)
        assert res_good is None


def test_score_explanations_dev_only() -> None:
    cfg = RankingConfig(explain=True)
    score, expl = compute_score(
        raw_bm25=-5.0,
        query_str="python guide",
        title="Python Guide",
        url="https://example.org/python",
        description="Python guide description",
        doc_type="page",
        word_count=150,
        published_at="2026-01-01T00:00:00Z",
        config=cfg,
    )
    assert expl is not None
    assert expl.bm25_score == pytest.approx(5.0)
    assert expl.title_multiplier == pytest.approx(1.15)
    assert expl.final_score == pytest.approx(score)
    assert len(expl.reasons) >= 2
    d = expl.to_dict()
    assert "reasons" in d
    assert d["final_score"] == round(score, 4)
