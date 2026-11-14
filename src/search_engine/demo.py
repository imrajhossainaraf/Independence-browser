"""End-to-end offline vertical slice demo: fixtures -> documents -> index -> search."""

from __future__ import annotations

import sys
from pathlib import Path

from search_engine.db import Database
from search_engine.extract.html import extract_html
from search_engine.indexer import compress_text, rebuild_index
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository
from search_engine.search import SearchService

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "html"


def run_demo(db_path: Path | None = None) -> int:
    """Run the offline vertical slice demo."""
    target_db = db_path or (Path("data") / "demo.db")
    target_db.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(" INDEPENDENT SEARCH ENGINE - OFFLINE VERTICAL SLICE DEMO")
    print("=" * 70)
    print(f"Database: {target_db.resolve()}")
    print(f"Fixtures: {FIXTURES_DIR.resolve()}")
    print()

    # 1. Parse fixtures and populate database
    print("==> 1. Ingesting HTML fixtures into SQLite documents store...")
    with Database(target_db) as db, db.transaction() as conn:
        hosts = HostRepository(conn)
        urls = UrlRepository(conn)
        docs = DocumentRepository(conn)

        host_id = hosts.ensure("example.org", "example.org")

        fixture_files = sorted(FIXTURES_DIR.glob("*.html"))
        if not fixture_files:
            print("ERROR: No HTML fixtures found in", FIXTURES_DIR, file=sys.stderr)
            return 1

        for fixture_path in fixture_files:
            html_bytes = fixture_path.read_bytes()
            url = f"https://example.org/fixtures/{fixture_path.stem}"
            url_id, _ = urls.add(url, host_id, source="seed")

            extracted = extract_html(html_bytes, final_url=url)
            headings_text = "; ".join(extracted.headings)

            docs.upsert_document(
                url_id=url_id,
                doc_type="page",
                final_url=extracted.final_url,
                canonical_url=extracted.canonical_url,
                title=extracted.title or fixture_path.stem.replace("_", " ").title(),
                headings=headings_text,
                description=extracted.description,
                text_gz=compress_text(extracted.text),
                lang=extracted.lang or "und",
                status="active" if extracted.is_indexable else "noindex",
                word_count=extracted.word_count,
                content_hash=extracted.content_hash,
                published_at=extracted.published_at,
            )

        print(f"    Ingested {len(fixture_files)} HTML fixtures into documents store.")

        # 2. Build FTS5 index
        print("==> 2. Rebuilding SQLite FTS5 inverted index (doc_fts + doc_fts_tri)...")
        report = rebuild_index(conn)
        print(
            f"    Indexed: {report.indexed_count} active documents in "
            f"{report.duration_seconds:.3f} s."
        )

    # 3. Execute sample queries offline
    print("\n==> 3. Running sample search queries offline...")
    demo_queries = [
        "spacecraft remote asteroid",
        "python asynchronous event loop",
        "scientific instruments telemetry",
    ]

    with Database(target_db) as db, db.reader() as conn:
        searcher = SearchService(conn)

        for q in demo_queries:
            print("\n" + "-" * 70)
            print(f"QUERY: {q!r}")
            response = searcher.search(q, limit=3)
            print(f"Match mode: {response.match_mode.upper()} | Found: {response.total_approx}")
            for i, result in enumerate(response.results, 1):
                print(f"  [{i}] {result.title}")
                print(f"      URL:   {result.url}")
                print(f"      Score: {result.score:.4f} | Word count: {result.word_count}")
                if result.description:
                    print(f"      Desc:  {result.description}")

    print("\n" + "=" * 70)
    print(" DEMO COMPLETED SUCCESSFULLY (All operations 100% offline)")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_demo())
