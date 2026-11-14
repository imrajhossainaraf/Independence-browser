"""Unit tests for the CLI search and demo commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from search_engine.cli import main
from search_engine.db import Database
from search_engine.indexer import compress_text, rebuild_index
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository


@pytest.fixture
def cli_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "cli_engine.db"
    db = Database(db_path).open()
    with db.transaction() as conn:
        hosts = HostRepository(conn)
        urls = UrlRepository(conn)
        docs = DocumentRepository(conn)
        host_id = hosts.ensure("cli.test", "cli.test")

        u1, _ = urls.add("https://cli.test/doc1", host_id, source="seed")
        docs.upsert_document(
            url_id=u1,
            doc_type="page",
            final_url="https://cli.test/doc1",
            title="Command Line Testing Guide",
            headings="CLI Arguments; Output Formatting",
            description="Testing CLI applications in Python.",
            text_gz=compress_text("Detailed guide on writing and testing CLI search commands."),
            lang="en",
            status="active",
        )

        u2, _ = urls.add("https://cli.test/manual.pdf", host_id, source="seed")
        docs.upsert_document(
            url_id=u2,
            doc_type="pdf",
            title="Command Line Interface Whitepaper",
            headings="Terminal Architecture",
            description="PDF manual about terminal programs.",
            text_gz=compress_text("In-depth analysis of command line interface performance."),
            lang="en",
            status="active",
        )

        rebuild_index(conn)
    db.close()
    return db_path


def test_cli_search_text_output(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--set", f"db_path={cli_db}", "search", "testing guide"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Search results for:" in captured.out
    assert "Command Line Testing Guide" in captured.out
    assert "https://cli.test/doc1" in captured.out


def test_cli_search_json_output(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["search", "testing guide", "--db", str(cli_db), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["query"] == "testing guide"
    assert data["match_mode"] in ("and", "or")
    assert len(data["results"]) >= 1
    assert data["results"][0]["title"] == "Command Line Testing Guide"


def test_cli_search_type_filter(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Filter by pdf only
    code = main(["search", "command line", "--type", "pdf", "--db", str(cli_db), "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data["results"]) == 1
    assert data["results"][0]["doc_type"] == "pdf"


def test_cli_search_database_not_found(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_db = tmp_path / "nonexistent.db"
    code = main(["search", "test", "--db", str(missing_db)])
    assert code == 1
    captured = capsys.readouterr()
    assert "database not found" in captured.err


def test_cli_demo_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    demo_db = tmp_path / "demo_test.db"
    code = main(["demo", "--db", str(demo_db)])
    assert code == 0
    captured = capsys.readouterr()
    assert "OFFLINE VERTICAL SLICE DEMO" in captured.out
    assert "DEMO COMPLETED SUCCESSFULLY" in captured.out


def test_cli_eval_command(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["eval", "--split", "dev", "--check-baseline", "eval/baseline.json"])
    assert code == 0
    captured = capsys.readouterr()
    assert "Search Evaluation Report [DEV SET]" in captured.out
    assert "Regression Check" in captured.out
    assert "PASSED" in captured.out

