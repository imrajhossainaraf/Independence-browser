"""Unit tests for SearchService: BM25 retrieval, AND/OR match modes, CJK trigram, and filters."""

from __future__ import annotations

from pathlib import Path

import pytest

from search_engine.db import Database
from search_engine.indexer import compress_text, rebuild_index
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository
from search_engine.search import MatchMode, SearchService


@pytest.fixture
def search_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "search_test.db").open()
    with db.transaction() as conn:
        hosts = HostRepository(conn)
        urls = UrlRepository(conn)
        docs = DocumentRepository(conn)
        host_id = hosts.ensure("example.org", "example.org")

        # 1. Python Async Guide (page, en)
        u1, _ = urls.add("https://example.org/python-async", host_id, source="seed")
        docs.upsert_document(
            url_id=u1,
            doc_type="page",
            title="Python Asynchronous Programming",
            headings="Event Loops; Coroutines; Tasks",
            description="Guide to asyncio in Python 3.",
            text_gz=compress_text("Asynchronous I/O with coroutines and tasks in modern Python."),
            lang="en",
            status="active",
        )

        # 2. Rust Systems Programming (page, en)
        u2, _ = urls.add("https://example.org/rust-systems", host_id, source="seed")
        docs.upsert_document(
            url_id=u2,
            doc_type="page",
            title="Systems Programming with Rust",
            headings="Memory Safety; Concurrency",
            description="High performance systems programming.",
            text_gz=compress_text("Rust memory model avoids data races in concurrent systems."),
            lang="en",
            status="active",
        )

        # 3. PDF Manual on Concurrency (pdf, en)
        u3, _ = urls.add("https://example.org/concurrency.pdf", host_id, source="seed")
        docs.upsert_document(
            url_id=u3,
            doc_type="pdf",
            title="Concurrency Engineering Whitepaper",
            headings="Parallelism vs Concurrency",
            description="PDF report on multi-threaded architectures.",
            text_gz=compress_text(
                "Detailed engineering paper on asynchronous systems and concurrency."
            ),
            lang="en",
            status="active",
        )

        # 4. French Cafe Guide (page, fr)
        u4, _ = urls.add("https://example.org/paris-cafe", host_id, source="seed")
        docs.upsert_document(
            url_id=u4,
            doc_type="page",
            title="Guide du Café et Bistro à Paris",
            headings="Bistrots Parisiens; Menus",
            description="Découvrez les meilleurs cafés de Paris.",
            text_gz=compress_text("Déguster un bon café traditionnel dans la capitale française."),
            lang="fr",
            status="active",
        )

        # 5. Chinese Climate Summit (page, zh)
        u5, _ = urls.add("https://example.org/china-climate", host_id, source="seed")
        docs.upsert_document(
            url_id=u5,
            doc_type="page",
            title="北京气候峰会",
            headings="可再生能源论坛",
            description="国际气候变化大会在北京召开。",
            text_gz=compress_text("各国代表在北京气候峰会上探讨可再生能源转型与碳排放中和目标。"),
            lang="zh",
            status="active",
        )

        # 6. Inactive document (should never be returned)
        u6, _ = urls.add("https://example.org/inactive-doc", host_id, source="seed")
        docs.upsert_document(
            url_id=u6,
            doc_type="page",
            title="Obsolete Python Guide",
            text_gz=compress_text("Legacy python 2 details."),
            status="inactive",
        )

        rebuild_index(conn)

    return db


def test_empty_query_returns_empty_response(search_db: Database) -> None:
    with search_db.reader() as conn:
        service = SearchService(conn)
        res = service.search("   ")
        assert res.match_mode == MatchMode.EMPTY.value
        assert res.results == []
        assert res.total_approx == 0


def test_and_match_mode(search_db: Database) -> None:
    with search_db.reader() as conn:
        service = SearchService(conn)
        res = service.search("python asynchronous coroutines")
        assert len(res.results) >= 1
        assert "Python" in res.results[0].title
        # Document 1 matches all 3 terms
        assert res.results[0].doc_id == 1


def test_or_fallback_mode(search_db: Database) -> None:
    # Query with terms that don't all appear in one doc: "python rust"
    # AND matches 0 docs, so it falls back to OR
    with search_db.reader() as conn:
        service = SearchService(conn)
        res = service.search("python rust concurrency")
        assert res.match_mode == MatchMode.OR.value
        assert len(res.results) >= 2


def test_cjk_query_uses_trigram(search_db: Database) -> None:
    with search_db.reader() as conn:
        service = SearchService(conn)
        res = service.search("北京气候峰会")
        assert res.match_mode == MatchMode.TRIGRAM.value
        assert len(res.results) == 1
        assert "北京" in res.results[0].title
        assert res.results[0].lang == "zh"


def test_filters_by_doc_type(search_db: Database) -> None:
    with search_db.reader() as conn:
        service = SearchService(conn)
        # Search for concurrency with doc_type="pdf"
        res_pdf = service.search("concurrency", doc_type="pdf")
        assert len(res_pdf.results) == 1
        assert res_pdf.results[0].doc_type == "pdf"

        # Search for concurrency with doc_type="page"
        res_page = service.search("concurrency", doc_type="page")
        assert len(res_page.results) >= 1
        for r in res_page.results:
            assert r.doc_type == "page"


def test_filters_by_language(search_db: Database) -> None:
    with search_db.reader() as conn:
        service = SearchService(conn)
        res_fr = service.search("cafe", lang="fr")
        assert len(res_fr.results) == 1
        assert res_fr.results[0].lang == "fr"

        res_en = service.search("cafe", lang="en")
        assert len(res_en.results) == 0


def test_inactive_documents_never_returned(search_db: Database) -> None:
    with search_db.reader() as conn:
        service = SearchService(conn)
        res = service.search("obsolete legacy")
        assert len(res.results) == 0
