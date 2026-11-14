"""Automated 10-point daily-use checklist per BUILD_PLAN.md Step 27.

Checklist:
1. Open home: verify top stories, latest, categories, data age
2. Read a story link: verify cluster headline, contributing source, and summary
3. Search: execute standard query and verify organic results
4. Read the synopsis: verify extractive answer and 100% verbatim grounding (Invariant 10)
5. Open sidebar source: verify preview or entity panel with title and source URL
6. Search offline: execute query in offline mode without network dependencies
7. Recover from an empty query: handle whitespace and empty inputs gracefully
8. Use a filter: restrict search by doc_type and verify result types
9. Follow a suggestion: evaluate did-you-mean suggestion for misspelled query
10. Restore from a backup: snapshot database, restore to fresh directory, verify integrity
"""

from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from search_engine.config import Settings
from search_engine.evaluation.evaluator import populate_eval_database
from search_engine.experience.home import HomeService
from search_engine.experience.models import HomePayload
from search_engine.indexer import decompress_text
from search_engine.operations.backup import create_backup, restore_backup
from search_engine.search import SearchResponse, SearchService


@dataclass(frozen=True, slots=True)
class ChecklistStepResult:
    step_num: int
    name: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DailyUseReport:
    passed: bool
    total_steps: int
    passed_steps: int
    results: list[ChecklistStepResult]

    def render_markdown(self) -> str:
        status_str = "PASS" if self.passed else "FAIL"
        lines = [
            f"# Daily-Use 10-Point Checklist Report [{status_str}]",
            f"**Passed**: {self.passed_steps}/{self.total_steps} steps",
            "",
            "| Step | Action | Status | Details |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for r in self.results:
            st = "PASS" if r.passed else "FAIL"
            lines.append(f"| {r.step_num} | {r.name} | {st} | {r.message} |")
        return "\n".join(lines)


def _populate_daily_use_db(conn: sqlite3.Connection) -> None:
    """Populate eval database and insert mock news sources and items for home page check."""
    from search_engine.db import discover_migrations, migrate

    migrate(conn, discover_migrations())
    populate_eval_database(conn)

    # Insert news sources and clusters for home testing
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO news_sources
                (id, feed_url_key, name, category, poll_interval_minutes, enabled, last_success_at)
            VALUES
                (1, 'https://news.example.com/rss', 'Daily Observer', 'Tech', 30, 1,
                 '2026-11-14T12:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO news_clusters (id, headline_item_id, created_at, updated_at)
            VALUES (1, 'news-daily-01', '2026-11-14T10:00:00Z', '2026-11-14T12:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO news_items
                (id, source_id, cluster_id, title, link, published_at, summary,
                 category, status, first_seen_at, updated_at)
            VALUES
                ('news-daily-01', 1, 1,
                 'Major Milestone Reached in Distributed Database Systems',
                 'https://news.example.com/dist-db', '2026-11-14T11:00:00Z',
                 'Researchers demonstrate linear scalability in consensus.',
                 'Tech', 'active', '2026-11-14T12:00:00Z', '2026-11-14T12:00:00Z')
            """
        )


def run_daily_use_checklist(conn: sqlite3.Connection | None = None) -> DailyUseReport:
    """Run all 10 checklist steps and return the DailyUseReport."""
    owns_conn = False
    if conn is None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _populate_daily_use_db(conn)
        owns_conn = True

    settings = Settings()
    results: list[ChecklistStepResult] = []

    # Step 1: Open home
    home_payload: HomePayload | None = None
    try:
        home_service = HomeService(settings)
        home_payload = home_service.get_home(conn)
        passed = (
            home_payload is not None
            and len(home_payload.top_stories) > 0
            and len(home_payload.latest) > 0
            and home_payload.data_age_seconds >= 0
        )
        msg = (
            f"Top stories: {len(home_payload.top_stories)}, "
            f"Latest: {len(home_payload.latest)}, "
            f"Data age: {home_payload.data_age_seconds}s"
        )
        results.append(ChecklistStepResult(1, "Open home", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(1, "Open home", False, f"Failed: {exc}"))

    # Step 2: Read story link
    try:
        if home_payload and home_payload.top_stories:
            story = home_payload.top_stories[0]
            passed = bool(
                story.headline and story.sources and story.summary and story.sources[0].url
            )
            msg = f"Headline: '{story.headline[:40]}...', Sources: {len(story.sources)}"
            results.append(ChecklistStepResult(2, "Read story link", passed, msg))
        else:
            results.append(ChecklistStepResult(2, "Read story link", False, "No top stories found"))
    except Exception as exc:
        results.append(ChecklistStepResult(2, "Read story link", False, f"Failed: {exc}"))

    # Step 3: Search
    search_service = SearchService(conn, settings)
    search_resp: SearchResponse | None = None
    try:
        search_resp = search_service.search("sqlite fts5 full text search", limit=5)
        passed = len(search_resp.results) > 0 and search_resp.total_approx > 0
        msg = (
            f"Results: {len(search_resp.results)}, "
            f"Total approx: {search_resp.total_approx}, "
            f"Match mode: {search_resp.match_mode}"
        )
        results.append(ChecklistStepResult(3, "Search", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(3, "Search", False, f"Failed: {exc}"))

    # Step 4: Read synopsis (Invariant 10: 100% verbatim grounded in document text)
    try:
        if search_resp and search_resp.synopsis:
            syn = search_resp.synopsis
            row = conn.execute(
                "SELECT text_gz FROM documents WHERE final_url = ?", (syn.url,)
            ).fetchone()
            if row and row["text_gz"]:
                doc_text = decompress_text(row["text_gz"])
                is_verbatim = syn.text in doc_text
                passed = is_verbatim and bool(syn.source_name and syn.url)
                msg = (
                    f"Verbatim grounded: {is_verbatim}, "
                    f"Source: '{syn.source_name}', "
                    f"Length: {len(syn.text)} chars"
                )
                results.append(ChecklistStepResult(4, "Read synopsis", passed, msg))
            else:
                results.append(
                    ChecklistStepResult(4, "Read synopsis", False, "Cited document text not found")
                )
        else:
            results.append(ChecklistStepResult(4, "Read synopsis", False, "No synopsis returned"))
    except Exception as exc:
        results.append(ChecklistStepResult(4, "Read synopsis", False, f"Failed: {exc}"))

    # Step 5: Open sidebar source
    try:
        if search_resp and search_resp.panel:
            p = search_resp.panel
            from search_engine.experience.models import PreviewCard

            if isinstance(p, PreviewCard):
                passed = bool(p.title and p.url and p.domain)
                msg = f"PreviewCard Title: '{p.title[:35]}...', Domain: {p.domain}"
            else:
                passed = bool(p.title and p.source_url)
                msg = f"EntityCard Title: '{p.title[:35]}...', Source: {p.source_name}"
            results.append(ChecklistStepResult(5, "Open sidebar source", passed, msg))
        else:
            results.append(
                ChecklistStepResult(5, "Open sidebar source", False, "No panel returned")
            )
    except Exception as exc:
        results.append(ChecklistStepResult(5, "Open sidebar source", False, f"Failed: {exc}"))

    # Step 6: Search offline
    try:
        offline_settings = Settings(offline_mode=True)
        offline_service = SearchService(conn, offline_settings)
        offline_resp = offline_service.search("python async programming", limit=5)
        passed = len(offline_resp.results) > 0 and offline_resp.total_approx > 0
        msg = f"Offline results: {len(offline_resp.results)}, Mode: {offline_resp.match_mode}"
        results.append(ChecklistStepResult(6, "Search offline", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(6, "Search offline", False, f"Failed: {exc}"))

    # Step 7: Recover from empty query
    try:
        empty_resp1 = search_service.search("")
        empty_resp2 = search_service.search("   ")
        passed = (
            len(empty_resp1.results) == 0
            and empty_resp1.total_approx == 0
            and empty_resp1.match_mode == "empty"
            and len(empty_resp2.results) == 0
            and empty_resp2.total_approx == 0
            and empty_resp2.match_mode == "empty"
        )
        msg = "Empty and whitespace inputs returned safe empty SearchResponse without error"
        results.append(ChecklistStepResult(7, "Recover from empty query", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(7, "Recover from empty query", False, f"Failed: {exc}"))

    # Step 8: Use filter
    try:
        filtered_resp = search_service.search("database", doc_type="page", limit=5)
        all_pages = all(r.doc_type == "page" for r in filtered_resp.results)
        passed = len(filtered_resp.results) > 0 and all_pages
        msg = f"Returned {len(filtered_resp.results)} results, all doc_type='page': {all_pages}"
        results.append(ChecklistStepResult(8, "Use filter", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(8, "Use filter", False, f"Failed: {exc}"))

    # Step 9: Follow suggestion
    try:
        misspelled_resp = search_service.search("pythn async programming")
        suggestion = misspelled_resp.suggestion
        if suggestion:
            corrected_resp = search_service.search(suggestion, limit=5)
            passed = len(corrected_resp.results) > 0
            msg = f"Suggestion '{suggestion}' returned {len(corrected_resp.results)} results"
            results.append(ChecklistStepResult(9, "Follow suggestion", passed, msg))
        else:
            # Check if auto-corrected directly
            passed = len(misspelled_resp.results) > 0
            msg = f"Auto-resolved {len(misspelled_resp.results)} results for query"
            results.append(ChecklistStepResult(9, "Follow suggestion", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(9, "Follow suggestion", False, f"Failed: {exc}"))

    # Step 10: Restore from backup
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            db_file = tmp_path / "live.db"
            raw_dir = tmp_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            backup_dir = tmp_path / "backup_out"
            restore_dir = tmp_path / "restore_out"

            # Save in-memory conn to disk DB for backup
            disk_conn = sqlite3.connect(db_file)
            conn.backup(disk_conn)
            disk_conn.close()

            # Execute backup
            _ = create_backup(
                db_path=db_file,
                output_dir=backup_dir,
                raw_dir=raw_dir,
            )
            # Execute restore
            restore_res = restore_backup(
                backup_dir=backup_dir,
                target_dir=restore_dir,
            )

            # Verify integrity
            verify_conn = sqlite3.connect(restore_res.database_path)
            verify_conn.row_factory = sqlite3.Row
            integrity = verify_conn.execute("PRAGMA integrity_check").fetchone()[0]
            restored_service = SearchService(verify_conn, settings)
            restored_search = restored_service.search("sqlite fts5 full text search", limit=5)
            verify_conn.close()

            expected_count = len(search_resp.results if search_resp else [])
            passed = (
                integrity == "ok"
                and len(restored_search.results) > 0
                and len(restored_search.results) == expected_count
            )
            msg = (
                f"PRAGMA integrity_check: {integrity}, "
                f"restored results: {len(restored_search.results)}"
            )
            results.append(ChecklistStepResult(10, "Restore from backup", passed, msg))
    except Exception as exc:
        results.append(ChecklistStepResult(10, "Restore from backup", False, f"Failed: {exc}"))

    if owns_conn:
        conn.close()

    passed_count = sum(1 for r in results if r.passed)
    all_passed = passed_count == len(results) and len(results) == 10

    return DailyUseReport(
        passed=all_passed,
        total_steps=len(results),
        passed_steps=passed_count,
        results=results,
    )
