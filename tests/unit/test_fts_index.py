"""Unit tests for Step 10: SQLite FTS5 Index (D4).

Covers:
- doc_fts (porter unicode61 remove_diacritics 2) and doc_fts_tri (trigram)
- weighted bm25() retrieval (title beats body-only)
- page, pdf, text, and news document types
- upsert, remove, deactivate operations
- noindex, metadata-only, inactive status handling
- empty index queries
- non-English text indexing (French, German, Chinese, Thai, Arabic)
- rebuild reproducibility from snapshot
- consistency verification and deliberate corruption detection
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from search_engine.db import Database
from search_engine.indexer import (
    DEFAULT_BM25_WEIGHTS,
    INDEX_VERSION,
    compress_text,
    deactivate_document,
    decompress_text,
    get_index_version,
    is_cjk_text,
    rebuild_index,
    remove_document_index,
    search_bm25,
    tokenize_url,
    upsert_document_index,
    verify_index_consistency,
)
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_path = tmp_path / "test_engine.db"
    return Database(db_path).open()


def _add_url(conn: sqlite3.Connection, url: str) -> int:
    hosts = HostRepository(conn)
    urls = UrlRepository(conn)
    host_id = hosts.ensure("example.com", "example.com")
    url_id, _ = urls.add(url, host_id, source="seed")
    return url_id


def test_empty_index_search(test_db: Database) -> None:
    with test_db.reader() as conn:
        results = search_bm25(conn, "anything")
        assert results == []

        consistency = verify_index_consistency(conn)
        assert consistency.is_consistent
        assert consistency.total_active_docs == 0
        assert consistency.doc_fts_count == 0


def test_tokenize_url() -> None:
    assert (
        tokenize_url("https://example.com/docs/python-guide.html?v=1")
        == "example com docs python guide html"
    )
    assert tokenize_url(None) == ""
    assert tokenize_url("invalid-url") == "invalid url"


def test_compress_and_decompress_text() -> None:
    text = "Hello world of search engines!"
    gz = compress_text(text)
    assert isinstance(gz, bytes)
    assert decompress_text(gz) == text
    assert decompress_text(None) == ""
    assert decompress_text(b"corrupt") == ""


def test_is_cjk_text() -> None:
    assert is_cjk_text("北京气候峰会") is True
    assert is_cjk_text("Tokyo 東京") is True
    assert is_cjk_text("English text only") is False


def test_known_term_retrieval(test_db: Database) -> None:
    with test_db.transaction() as conn:
        url_id = _add_url(conn, "https://example.com/sqlite-fts5")
        docs = DocumentRepository(conn)
        doc_id = docs.upsert_document(
            url_id=url_id,
            doc_type="page",
            title="SQLite FTS5 Full Text Guide",
            headings="Virtual Tables; Tokenizers",
            description="Comprehensive guide to SQLite full-text search.",
            text_gz=compress_text(
                "FTS5 provides fast inverted index retrieval in SQLite databases."
            ),
            final_url="https://example.com/sqlite-fts5",
        )
        upsert_document_index(conn, doc_id)

    with test_db.reader() as conn:
        results = search_bm25(conn, "sqlite fts5")
        assert len(results) == 1
        assert results[0][0] == doc_id
        assert results[0][1] < 0.0  # SQLite BM25 scores are negative


def test_title_beats_body_only(test_db: Database) -> None:
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)

        # Doc 1: query term "quantum" in title
        u1 = _add_url(conn, "https://example.com/doc1")
        doc1_id = docs.upsert_document(
            url_id=u1,
            doc_type="page",
            title="Quantum Computing Breakthrough",
            headings="Hardware; Qubits",
            description="Overview of quantum systems.",
            text_gz=compress_text("Researchers demonstrated superconducting qubits."),
        )
        upsert_document_index(conn, doc1_id)

        # Doc 2: query term "quantum" only in body
        u2 = _add_url(conn, "https://example.com/doc2")
        doc2_id = docs.upsert_document(
            url_id=u2,
            doc_type="page",
            title="Modern Semiconductor Technology",
            headings="Silicon Wafers; Transistors",
            description="Overview of microelectronics.",
            text_gz=compress_text(
                "Looking ahead, quantum effects require new semiconductor designs quantum."
            ),
        )
        upsert_document_index(conn, doc2_id)

    with test_db.reader() as conn:
        results = search_bm25(conn, "quantum", weights=DEFAULT_BM25_WEIGHTS)
        assert len(results) == 2
        # Doc 1 has "quantum" in title (weight 10.0), so BM25 score must be lower (more negative)
        assert results[0][0] == doc1_id
        assert results[1][0] == doc2_id
        assert results[0][1] < results[1][1]


def test_upsert_replaces_text(test_db: Database) -> None:
    with test_db.transaction() as conn:
        u = _add_url(conn, "https://example.com/updatable")
        docs = DocumentRepository(conn)
        doc_id = docs.upsert_document(
            url_id=u,
            doc_type="page",
            title="Initial Title About Apples",
            text_gz=compress_text("Orchards and fruit picking apples."),
        )
        upsert_document_index(conn, doc_id)

    with test_db.reader() as conn:
        assert len(search_bm25(conn, "apples")) == 1
        assert len(search_bm25(conn, "oranges")) == 0

    # Update document to discuss oranges instead
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)
        docs.upsert_document(
            url_id=u,
            doc_type="page",
            title="Updated Title About Oranges",
            text_gz=compress_text("Citrus groves and harvesting fresh oranges."),
        )
        upsert_document_index(conn, doc_id)

    with test_db.reader() as conn:
        assert len(search_bm25(conn, "apples")) == 0
        assert len(search_bm25(conn, "oranges")) == 1
        assert search_bm25(conn, "oranges")[0][0] == doc_id


def test_removal_works(test_db: Database) -> None:
    with test_db.transaction() as conn:
        u = _add_url(conn, "https://example.com/removable")
        docs = DocumentRepository(conn)
        doc_id = docs.upsert_document(
            url_id=u,
            doc_type="page",
            title="Ephemeral Document",
            text_gz=compress_text("Content that will be deleted."),
        )
        upsert_document_index(conn, doc_id)

    with test_db.reader() as conn:
        assert len(search_bm25(conn, "ephemeral")) == 1

    with test_db.transaction() as conn:
        removed = remove_document_index(conn, doc_id)
        assert removed is True

    with test_db.reader() as conn:
        assert len(search_bm25(conn, "ephemeral")) == 0


def test_deactivate_document(test_db: Database) -> None:
    with test_db.transaction() as conn:
        u = _add_url(conn, "https://example.com/deactivatable")
        docs = DocumentRepository(conn)
        doc_id = docs.upsert_document(
            url_id=u,
            doc_type="page",
            title="Active Page",
            text_gz=compress_text("Active page content here."),
        )
        upsert_document_index(conn, doc_id)
        deactivate_document(conn, doc_id)

    with test_db.reader() as conn:
        assert len(search_bm25(conn, "active")) == 0
        row = conn.execute(
            "SELECT status, indexed_version FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        assert row["status"] == "inactive"
        assert row["indexed_version"] == 0


def test_noindex_and_metadata_only_status_rules(test_db: Database) -> None:
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)

        # 1. noindex
        u1 = _add_url(conn, "https://example.com/noindex")
        d1 = docs.upsert_document(
            url_id=u1,
            title="Secret Page",
            text_gz=compress_text("Do not index this content."),
            status="noindex",
        )
        assert upsert_document_index(conn, d1) is False

        # 2. metadata_only
        u2 = _add_url(conn, "https://example.com/meta")
        d2 = docs.upsert_document(
            url_id=u2,
            title="Scanned PDF",
            text_gz=compress_text("Unextractable scan."),
            status="metadata_only",
        )
        assert upsert_document_index(conn, d2) is False

        # 3. unsupported
        u3 = _add_url(conn, "https://example.com/unsupported")
        d3 = docs.upsert_document(
            url_id=u3,
            title="Binary archive",
            text_gz=compress_text("Archive content."),
            status="unsupported",
        )
        assert upsert_document_index(conn, d3) is False

    with test_db.reader() as conn:
        assert len(search_bm25(conn, "secret")) == 0
        assert len(search_bm25(conn, "scanned")) == 0
        assert len(search_bm25(conn, "archive")) == 0

        # Consistency should show 0 active documents
        rep = verify_index_consistency(conn)
        assert rep.is_consistent
        assert rep.total_active_docs == 0


def test_document_types_handling(test_db: Database) -> None:
    """Verifies page, pdf, text, and news document types index properly."""
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)

        # 1. page
        u1 = _add_url(conn, "https://example.com/page")
        docs.upsert_document(
            url_id=u1,
            doc_type="page",
            title="Web Page Article",
            text_gz=compress_text("Standard HTML article body."),
        )

        # 2. pdf
        u2 = _add_url(conn, "https://example.com/whitepaper.pdf")
        d2 = docs.upsert_document(
            url_id=u2,
            doc_type="pdf",
            title="Technical Whitepaper PDF",
            text_gz=compress_text("PDF extracted text section."),
        )

        # 3. text
        u3 = _add_url(conn, "https://example.com/readme.txt")
        docs.upsert_document(
            url_id=u3,
            doc_type="text",
            title="Plain Text Readme",
            text_gz=compress_text("Raw text file documentation."),
        )

        # 4. news item
        conn.execute(
            """
            INSERT INTO news_sources (id, feed_url_key, name, category, poll_interval_minutes)
            VALUES (1, 'feed1', 'TechNews', 'tech', 30)
            """
        )
        conn.execute(
            """
            INSERT INTO news_items (
                id, source_id, title, link, published_at, summary, category,
                first_seen_at, updated_at
            )
            VALUES (
                'news1', 1, 'Breaking Tech News Headline', 'https://example.com/news1',
                '2026-11-14T00:00:00Z', 'Feed summary text for news.', 'tech',
                '2026-11-14T00:00:00Z', '2026-11-14T00:00:00Z'
            )
            """
        )
        d4 = docs.upsert_document(
            news_item_id="news1",
            doc_type="news",
            title="Breaking Tech News Headline",
            description="Feed summary text for news.",
        )

        build_report = rebuild_index(conn)
        assert build_report.indexed_count == 4
        assert build_report.by_type == {"page": 1, "pdf": 1, "text": 1, "news": 1}

    with test_db.reader() as conn:
        assert search_bm25(conn, "whitepaper")[0][0] == d2
        assert search_bm25(conn, "breaking news")[0][0] == d4


def test_non_english_multilingual_indexing(test_db: Database) -> None:
    """Verifies French, German, Chinese, Thai, and Arabic index and query without crashing."""
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)

        # French with accents
        u1 = _add_url(conn, "https://example.com/french")
        d1 = docs.upsert_document(
            url_id=u1,
            title="Guide du Café et Restaurant",
            text_gz=compress_text(
                "Déguster un délicieux café dans un bistrot parisien résumé gastronomique."
            ),
            lang="fr",
        )

        # German with umlauts
        u2 = _add_url(conn, "https://example.com/german")
        d2 = docs.upsert_document(
            url_id=u2,
            title="Reiseführer München und Berlin",
            text_gz=compress_text("Entdecken Sie München und die bayerische Kultur."),
            lang="de",
        )

        # Chinese CJK
        u3 = _add_url(conn, "https://example.com/chinese")
        d3 = docs.upsert_document(
            url_id=u3,
            title="北京气候峰会",
            text_gz=compress_text("北京召开国际气候峰会，聚焦全球绿色可再生能源发展。"),
            lang="zh",
        )

        # Thai
        u4 = _add_url(conn, "https://example.com/thai")
        d4 = docs.upsert_document(
            url_id=u4,
            title="การค้นหาข้อความ",
            text_gz=compress_text("ระบบการค้นหาข้อความและดัชนีภาษาไทยที่มีประสิทธิภาพสูง"),
            lang="th",
        )

        # Arabic
        u5 = _add_url(conn, "https://example.com/arabic")
        d5 = docs.upsert_document(
            url_id=u5,
            title="محرك البحث الذكي",
            text_gz=compress_text("تطوير محرك بحث عربي متقدم للوصول إلى المعلومات بسهولة"),
            lang="ar",
        )

        rebuild_index(conn)

    with test_db.reader() as conn:
        # 1. French accent folding (cafe matches café)
        res_fr = search_bm25(conn, "cafe restaurant")
        assert len(res_fr) > 0
        assert res_fr[0][0] == d1

        # 2. German diacritic folding (munchen matches München)
        res_de = search_bm25(conn, "munchen berlin")
        assert len(res_de) > 0
        assert res_de[0][0] == d2

        # 3. Chinese CJK trigram fallback
        res_zh = search_bm25(conn, "北京气候峰会")
        assert len(res_zh) > 0
        assert res_zh[0][0] == d3

        # 4. Thai trigram fallback
        res_th = search_bm25(conn, "การค้นหา", table="doc_fts_tri")
        assert len(res_th) > 0
        assert res_th[0][0] == d4

        # 5. Arabic unicode indexing
        res_ar = search_bm25(conn, "محرك البحث")
        assert len(res_ar) > 0
        assert res_ar[0][0] == d5


def test_rebuild_from_snapshot_reproduces_result_order(test_db: Database) -> None:
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)
        for i in range(1, 15):
            u = _add_url(conn, f"https://example.com/page{i}")
            docs.upsert_document(
                url_id=u,
                doc_type="page",
                title=f"Distributed Systems Architecture Chapter {i}",
                headings=f"Section {i} A; Section {i} B",
                description=f"Overview of distributed computing part {i}.",
                text_gz=compress_text(
                    f"Consensus algorithms and replication across nodes iteration {i}."
                ),
            )
        rebuild_index(conn)

    query = "distributed systems consensus replication"
    with test_db.reader() as conn:
        initial_results = search_bm25(conn, query, limit=10)
        assert len(initial_results) == 10

    # Rebuild index again from same snapshot
    with test_db.transaction() as conn:
        report = rebuild_index(conn)
        assert report.indexed_count == 14
        assert get_index_version(conn) == INDEX_VERSION

    with test_db.reader() as conn:
        rebuilt_results = search_bm25(conn, query, limit=10)
        assert len(rebuilt_results) == 10
        # Results and order must be 100% identical
        assert [r[0] for r in rebuilt_results] == [r[0] for r in initial_results]
        assert [r[1] for r in rebuilt_results] == [r[1] for r in initial_results]


def test_consistency_check_detects_deliberate_corruption(test_db: Database) -> None:
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)
        u1 = _add_url(conn, "https://example.com/c1")
        u2 = _add_url(conn, "https://example.com/c2")
        d1 = docs.upsert_document(url_id=u1, title="Doc 1", text_gz=compress_text("Body 1"))
        docs.upsert_document(url_id=u2, title="Doc 2", text_gz=compress_text("Body 2"))
        rebuild_index(conn)

    with test_db.reader() as conn:
        report = verify_index_consistency(conn)
        assert report.is_consistent
        assert report.total_active_docs == 2
        assert report.doc_fts_count == 2
        assert report.doc_fts_tri_count == 2

    # Deliberate corruption 1: Delete a row from doc_fts out-of-band (missing row in FTS)
    with test_db.transaction() as conn:
        conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d1,))

    with test_db.reader() as conn:
        corrupted_report = verify_index_consistency(conn)
        assert corrupted_report.is_consistent is False
        assert d1 in corrupted_report.missing_in_fts

    # Deliberate corruption 2: Insert an orphan row into doc_fts (orphan row not in documents)
    with test_db.transaction() as conn:
        conn.execute(
            "INSERT INTO doc_fts (rowid, title, headings, description, body, url_tokens) "
            "VALUES (9999, 'Orphan', '', '', '', '')"
        )

    with test_db.reader() as conn:
        orphan_report = verify_index_consistency(conn)
        assert orphan_report.is_consistent is False
        assert 9999 in orphan_report.orphans_in_fts
        rendered = orphan_report.render()
        assert "FAILED" in rendered
        assert "9999" in rendered

    # Self-healing: rebuild_index restores full consistency
    with test_db.transaction() as conn:
        rebuild_index(conn)

    with test_db.reader() as conn:
        healed_report = verify_index_consistency(conn)
        assert healed_report.is_consistent is True
        assert healed_report.missing_in_fts == []
        assert healed_report.orphans_in_fts == []


def test_fts5vocab_populated(test_db: Database) -> None:
    with test_db.transaction() as conn:
        docs = DocumentRepository(conn)
        u = _add_url(conn, "https://example.com/vocab")
        d = docs.upsert_document(
            url_id=u,
            title="Vocabulary Harvest",
            text_gz=compress_text("Harvesting unique linguistic tokens."),
        )
        upsert_document_index(conn, d)

    with test_db.reader() as conn:
        rows = conn.execute("SELECT term, doc, cnt FROM doc_fts_vocab ORDER BY term ASC").fetchall()
        terms = {r["term"] for r in rows}
        assert "harvest" in terms or "linguist" in terms or "token" in terms
