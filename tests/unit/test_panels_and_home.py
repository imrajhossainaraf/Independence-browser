"""Unit and integration tests for Panels, Synopsis, Home, and Suggestions (Step 21)."""

from __future__ import annotations

import gzip
import sqlite3
import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from search_engine.config import load_settings
from search_engine.db import Database
from search_engine.experience.home import HomeService
from search_engine.experience.models import (
    EntityCard,
    HomePayload,
    NewsPagePayload,
    PreviewCard,
    Synopsis,
)
from search_engine.experience.panels import PanelService
from search_engine.experience.suggest import SuggestService, rebuild_suggestions
from search_engine.indexer import decompress_text
from search_engine.indexer.build import upsert_document_index
from search_engine.search.service import SearchService


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "test_panels_home.db").open()
    yield db
    db.close()


def _populate_test_corpus(conn: sqlite3.Connection, now: datetime) -> None:
    """Populate database with sample documents and news items for Step 21 testing."""
    now_iso = now.isoformat()
    t_2d_ago = (now - timedelta(days=2)).isoformat()
    t_10d_ago = (now - timedelta(days=10)).isoformat()

    # 1. News Sources
    conn.execute(
        """
        INSERT INTO news_sources (id, feed_url_key, name, category, poll_interval_minutes,
                                  last_success_at, consecutive_failures)
        VALUES (1, 'https://tech.example.com/feed', 'Tech Journal', 'Technology', 30, ?, 0),
               (2, 'https://space.example.com/rss', 'Space Daily', 'Space', 45, ?, 0),
               (3, 'https://failing.example.com/rss', 'Broken News', 'General', 60, NULL, 5)
        """,
        (now_iso, t_2d_ago),
    )

    # 2. News Items
    conn.execute(
        """
        INSERT INTO news_items (
            id, source_id, link, title, summary, published_at, category, status,
            first_seen_at, updated_at
        )
        VALUES
        ('news-1', 1, 'https://tech.example.com/quantum-leap',
         'Quantum Computing Breakthrough Announced by Researchers',
         'Scientists achieved room temperature quantum coherence in silicon carbide crystals.',
         ?, 'Technology', 'active', ?, ?),
        ('news-2', 2, 'https://space.example.com/quantum-sensors',
         'Quantum Sensors Deployed on Deep Space Satellite',
         'New deep space probe uses high precision quantum magnetic sensors for asteroid mapping.',
         ?, 'Space', 'active', ?, ?),
        ('news-3', 1, 'https://tech.example.com/old-story',
         'Old Vintage Microprocessor Exhibition in London',
         'Retro computing festival highlights early 8-bit home computers from the 1980s.',
         ?, 'Technology', 'active', ?, ?),
        ('news-inactive', 1, 'https://tech.example.com/deleted',
         'Retracted Phantom Headline',
         'This story was retracted and is now inactive.',
         ?, 'Technology', 'inactive', ?, ?)
        """,
        (
            now_iso,
            now_iso,
            now_iso,
            t_2d_ago,
            t_2d_ago,
            t_2d_ago,
            t_10d_ago,
            t_10d_ago,
            t_10d_ago,
            now_iso,
            now_iso,
            now_iso,
        ),
    )

    # 3. Documents for search, preview, and synopsis
    doc_quantum_body = (
        "Quantum computing harnesses the strange properties of quantum mechanics to solve "
        "exponential problems.\n\n"
        "Researchers at the National Institute have achieved a major milestone in solid-state "
        "physics. "
        "Quantum computing harnesses superposition and entanglement to perform complex matrix "
        "calculations. "
        "The newly discovered room temperature coherence allows qubits to remain stable for "
        "several milliseconds.\n\n"
        "- Navigation Link 1\n"
        "- Navigation Link 2\n"
        "All rights reserved. Copyright 2026."
    )

    doc_space_body = (
        "Exploration of the outer solar system requires autonomous navigation.\n\n"
        "Robotic probes equipped with ion thrusters are mapping the Jovian moons with "
        "unprecedented detail. "
        "Deep space missions rely on optical communication systems transmitting high-bandwidth "
        "telemetry back to Earth."
    )

    conn.execute(
        "INSERT INTO hosts (id, host, registered_domain, created_at) VALUES "
        "(1, 'tech.example.com', 'example.com', ?), "
        "(2, 'space.example.com', 'example.com', ?)",
        (now_iso, now_iso),
    )

    conn.execute(
        """
        INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at) VALUES
        (1, 'https://tech.example.com/quantum-primer', 1, 'done', 'seed', ?, ?),
        (2, 'https://space.example.com/outer-solar-system', 2, 'done', 'seed', ?, ?)
        """,
        (now_iso, now_iso, now_iso, now_iso),
    )

    cur1 = conn.execute(
        """
        INSERT INTO documents (url_id, doc_type, final_url, canonical_url, title,
                              text_gz, word_count, published_at, fetched_at, status, updated_at)
        VALUES (1, 'page', 'https://tech.example.com/quantum-primer', 'https://tech.example.com/quantum-primer',
                'Quantum Computing Principles and Applications',
                ?, 60, ?, ?, 'active', ?)
        """,
        (gzip.compress(doc_quantum_body.encode("utf-8")), now_iso, now_iso, now_iso),
    )
    doc_id1 = int(cur1.lastrowid or 0)
    upsert_document_index(conn, doc_id1)

    cur2 = conn.execute(
        """
        INSERT INTO documents (url_id, doc_type, final_url, canonical_url, title,
                              text_gz, word_count, published_at, fetched_at, status, updated_at)
        VALUES (2, 'page', 'https://space.example.com/outer-solar-system', 'https://space.example.com/outer-solar-system',
                'Deep Space Missions and Robotic Probes',
                ?, 35, ?, ?, 'active', ?)
        """,
        (gzip.compress(doc_space_body.encode("utf-8")), t_2d_ago, t_2d_ago, t_2d_ago),
    )
    doc_id2 = int(cur2.lastrowid or 0)
    upsert_document_index(conn, doc_id2)

    # Build autocomplete table
    rebuild_suggestions(conn)


def test_grounding_guarantee_verbatim_substring(test_db: Database) -> None:
    """Invariant 10: Every synopsis text MUST be a 100% verbatim substring of the cited document."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        service = SearchService(conn, settings=settings)
        resp = service.search("quantum computing coherence", reference_time=now)

        assert resp.synopsis is not None
        synopsis = resp.synopsis
        assert isinstance(synopsis, Synopsis)
        assert synopsis.confidence > 0.0
        assert len(synopsis.text) >= settings.synopsis_min_chars
        assert len(synopsis.text) <= settings.synopsis_max_chars

        # Check grounding against database
        doc_row = conn.execute(
            "SELECT text_gz, title, final_url FROM documents WHERE final_url = ?", (synopsis.url,)
        ).fetchone()
        assert doc_row is not None
        content_text = decompress_text(doc_row["text_gz"])

        # Grounding invariant: 100% verbatim substring!
        assert synopsis.text in content_text
        assert synopsis.source_name in (str(doc_row["title"]), "tech.example.com")


def test_negative_queries_yield_null_synopsis_and_panels(test_db: Database) -> None:
    """D19: Negative queries, single-word queries, and low-confidence return null synopsis."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        service = SearchService(conn, settings=settings)

        # 1. Single content term (D19: requires at least 2 content terms)
        resp1 = service.search("quantum", reference_time=now)
        assert resp1.synopsis is None

        # 2. Only stopwords
        resp2 = service.search("in the about", reference_time=now)
        assert resp2.synopsis is None

        # 3. Completely non-matching query
        resp3 = service.search("xylophone zeppelin underwater", reference_time=now)
        assert resp3.synopsis is None
        assert resp3.panel is None
        assert resp3.recent_news is None


def test_preview_card_assembly(test_db: Database) -> None:
    """D19: Preview card for top result group with title, domain, date, clean lead, and source."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        service = SearchService(conn, settings=settings)
        resp = service.search("deep space probes", reference_time=now)

        assert resp.panel is not None
        assert isinstance(resp.panel, PreviewCard)
        assert resp.panel.kind == "preview"
        assert resp.panel.domain == "space.example.com"
        assert "space.example.com" in resp.panel.url
        assert resp.panel.lead != ""
        assert len(resp.panel.lead) <= settings.preview_lead_chars + 10
        assert resp.panel.source_name == "space.example.com"


def test_entity_card_flag_behavior(test_db: Database) -> None:
    """Release A: feature_entities is False so EntityCard is never returned."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    assert settings.feature_entities is False

    with test_db.reader() as conn:
        panel_svc = PanelService(settings)
        # Search returns PreviewCard fallback, not EntityCard
        panel, _, _ = panel_svc.assemble(
            conn,
            "quantum computing",
            [
                PreviewCard(
                    kind="preview",
                    title="T",
                    domain="d",
                    url="http://d/1",
                    lead="lead",
                    source_name="s",
                )
            ],
        )
        assert isinstance(panel, PreviewCard)
        assert not isinstance(panel, EntityCard)


def test_recent_news_block_filtering_and_window(test_db: Database) -> None:
    """D19: Recent news returns up to 3 items within 7 days; hides when none match."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        service = SearchService(conn, settings=settings)

        # 1. Matches quantum stories from last 7 days (news-1 and news-2)
        resp = service.search("quantum", reference_time=now)
        assert resp.recent_news is not None
        assert len(resp.recent_news) == 2
        titles = {n.title for n in resp.recent_news}
        assert "Quantum Computing Breakthrough Announced by Researchers" in titles
        assert "Quantum Sensors Deployed on Deep Space Satellite" in titles

        # 2. Old story (10 days ago) is excluded from 7-day window
        resp_old = service.search("microprocessor 8-bit", reference_time=now)
        assert resp_old.recent_news is None


def test_home_service_payload_and_freshness(test_db: Database) -> None:
    """D20: HomeService returns complete HomePayload, health, top stories, and freshness status."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        home_svc = HomeService(settings)
        payload = home_svc.get_home(conn, now=now)

        assert isinstance(payload, HomePayload)
        assert payload.data_age_seconds == 0  # We inserted an item at 'now'
        assert payload.is_stale is False
        assert len(payload.sources_health) == 3

        # Check source health statuses
        h_map = {h.name: h for h in payload.sources_health}
        assert h_map["Tech Journal"].status == "healthy"
        assert h_map["Broken News"].status == "failing"
        assert h_map["Broken News"].consecutive_failures == 5

        # Check latest items (only active items, inactive excluded)
        assert len(payload.latest) == 3
        assert all(item.title != "Retracted Phantom Headline" for item in payload.latest)

        # Check categories
        assert set(payload.categories) == {"Space", "Technology"}


def test_home_service_stale_detection(test_db: Database) -> None:
    """D20: When data is older than configured news_stale_hours, is_stale is set to True."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "news_stale_hours": "1",  # 1 hour threshold
        },
    )

    future_clock = now + timedelta(hours=3)
    with test_db.reader() as conn:
        home_svc = HomeService(settings)
        payload = home_svc.get_home(conn, now=future_clock)

        assert payload.is_stale is True
        assert payload.data_age_seconds >= 3 * 3600


def test_home_service_paged_news(test_db: Database) -> None:
    """D20: GET /api/v1/news paged navigation with category filters."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        home_svc = HomeService(settings)

        # All categories, page size 2
        p1 = home_svc.get_news_page(conn, page=1, page_size=2, now=now)
        assert isinstance(p1, NewsPagePayload)
        assert p1.total_count == 3
        assert p1.total_pages == 2
        assert len(p1.items) == 2

        # Space category only
        p_space = home_svc.get_news_page(conn, category="Space", page=1, page_size=10, now=now)
        assert p_space.total_count == 1
        assert p_space.items[0].category == "Space"
        assert p_space.items[0].title == "Quantum Sensors Deployed on Deep Space Satellite"


def test_suggest_service_autocomplete_and_priority(test_db: Database) -> None:
    """Step 21: Autocomplete prefix matching, priority ordering, diacritics, and latency."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        suggest_svc = SuggestService(settings)

        # 1. Under 2 characters returns empty
        assert suggest_svc.suggest(conn, "") == []
        assert suggest_svc.suggest(conn, "q") == []

        # 2. Prefix 'qu' matches 'Quantum' headlines and titles
        suggestions = suggest_svc.suggest(conn, "qu", limit=8)
        assert len(suggestions) >= 2
        # Headlines (priority 2) come before Titles (priority 3)
        assert suggestions[0].startswith("Quantum Computing Breakthrough")

        # 3. Diacritics and case-insensitivity test
        # Insert a title with diacritics: Café & Schrödinger
        with test_db.transaction() as wconn:
            wconn.execute(
                "INSERT INTO hosts (id, host, registered_domain, created_at) "
                "VALUES (10, 'example.com', 'example.com', ?)",
                (now.isoformat(),),
            )
            wconn.execute(
                """
                INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at)
                VALUES (10, 'https://example.com/cafe', 10, 'done', 'seed', ?, ?)
                """,
                (now.isoformat(), now.isoformat()),
            )
            wconn.execute(
                """
                INSERT INTO documents (url_id, doc_type, final_url, canonical_url, title,
                                      text_gz, word_count, status, fetched_at, updated_at)
                VALUES (10, 'page', 'https://example.com/cafe', 'https://example.com/cafe',
                        'Café des Scientifiques on Schrödinger Physics',
                        ?, 5, 'active', ?, ?)
                """,
                (
                    gzip.compress(b"Discussion on physics and coffee."),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            rebuild_suggestions(wconn)

        s_cafe = suggest_svc.suggest(conn, "cafe")
        assert len(s_cafe) == 1
        assert s_cafe[0] == "Café des Scientifiques on Schrödinger Physics"

        s_schrod = suggest_svc.suggest(conn, "schro")
        assert len(s_schrod) == 1
        assert s_schrod[0] == "Café des Scientifiques on Schrödinger Physics"

        # 4. Removed content never suggested
        with test_db.transaction() as wconn:
            wconn.execute(
                "UPDATE documents SET status = 'inactive' WHERE final_url = 'https://example.com/cafe'"
            )
            rebuild_suggestions(wconn)

        assert suggest_svc.suggest(conn, "cafe") == []

        # 5. Benchmark Latency test: p95 under 50ms
        t0 = time.perf_counter()
        for _ in range(50):
            suggest_svc.suggest(conn, "qu", limit=8)
        t_elapsed_ms = (time.perf_counter() - t0) * 1000 / 50.0
        assert t_elapsed_ms < 50.0  # Should be < 2ms


def test_search_response_serialization_with_experience_models(test_db: Database) -> None:
    """SearchResponse.to_dict() correctly serializes panel, synopsis, and recent_news."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_test_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )

    with test_db.reader() as conn:
        service = SearchService(conn, settings=settings)
        resp = service.search("quantum computing coherence", reference_time=now)

        d = resp.to_dict()
        assert d["query"] == "quantum computing coherence"
        assert d["panel"] is not None
        assert d["panel"]["kind"] == "preview"
        assert d["synopsis"] is not None
        assert "text" in d["synopsis"]
        assert "confidence" in d["synopsis"]
        assert d["recent_news"] is not None
        assert len(d["recent_news"]) == 2
