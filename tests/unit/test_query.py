"""Unit tests for query parsing, normalization, and hypothesis fuzz testing (Decision D5)."""

from __future__ import annotations

import sqlite3

from hypothesis import given, settings
from hypothesis import strategies as st

from search_engine.search.query import (
    DEFAULT_STOPWORDS,
    MAX_QUERY_LENGTH,
    MAX_QUERY_TERMS,
    build_fts5_and_expression,
    build_fts5_or_expression,
    normalize_query_text,
    parse_query,
)


def test_normalize_query_text_nfkc_and_casefold() -> None:
    # Full-width characters converted to ASCII via NFKC
    raw = "Ｐｙｔｈｏｎ  Ａｓｙｎｃ   "
    assert normalize_query_text(raw) == "python async"

    # Diacritics preserved for porter unicode61 remove_diacritics 2
    accented = "CAFÉ   RÉSUMÉ"
    assert normalize_query_text(accented) == "café résumé"


def test_normalize_query_text_strips_control_characters() -> None:
    # Null bytes, control chars (0x00 to 0x1F, DEL 0x7F)
    raw = "hello\x00world\x07test\x1fhere\x7fend"
    assert normalize_query_text(raw) == "hello world test here end"


def test_normalize_query_text_length_limit() -> None:
    long_query = "word " * 100
    norm = normalize_query_text(long_query)
    assert len(norm) <= MAX_QUERY_LENGTH


def test_parse_query_term_limit() -> None:
    many_terms = " ".join(f"term{i}" for i in range(50))
    parsed = parse_query(many_terms)
    assert parsed.term_count <= MAX_QUERY_TERMS
    assert len(parsed.tokens) == MAX_QUERY_TERMS


def test_parse_query_quoted_phrases() -> None:
    raw = 'sqlite fts5 "full text search" guide "inverted index"'
    parsed = parse_query(raw)

    terms = parsed.terms_for_and()
    assert "sqlite" in terms
    assert "fts5" in terms
    assert "full text search" in terms
    assert "guide" in terms
    assert "inverted index" in terms

    phrase_tokens = [t for t in parsed.tokens if t.is_phrase]
    assert len(phrase_tokens) == 2
    assert phrase_tokens[0].text == "full text search"
    assert phrase_tokens[1].text == "inverted index"


def test_parse_query_unbalanced_quotes() -> None:
    raw = 'sqlite "fts5 search without closing quote'
    parsed = parse_query(raw)
    assert not parsed.is_empty
    # Unbalanced quotes don't crash and parse words safely
    terms = parsed.terms_for_and()
    assert "sqlite" in terms


def test_terms_for_or_removes_stopwords() -> None:
    raw = "the quick brown fox is running in a green field"
    parsed = parse_query(raw)

    and_terms = parsed.terms_for_and()
    or_terms = parsed.terms_for_or(DEFAULT_STOPWORDS)

    assert "the" in and_terms
    assert "is" in and_terms
    assert "the" not in or_terms
    assert "is" not in or_terms
    assert "quick" in or_terms
    assert "brown" in or_terms
    assert "fox" in or_terms


def test_terms_for_or_all_stopwords_fallback() -> None:
    # If a query consists only of stopwords, don't drop all of them
    raw = "the of and in"
    parsed = parse_query(raw)
    or_terms = parsed.terms_for_or(DEFAULT_STOPWORDS)
    assert len(or_terms) > 0


def test_hostile_fts5_syntax_strings_never_raise() -> None:
    """Prove that malicious or malformed user query syntax strings never break FTS5."""
    hostile_queries = [
        '""',
        '"""',
        '"""""',
        "AND OR NOT NEAR",
        "NEAR(foo bar, 10)",
        "title:secret",
        "headings:admin",
        "column:value",
        "SELECT * FROM doc_fts;",
        "'; DROP TABLE doc_fts; --",
        "( ( ( unbalanced ) )",
        "*** * *",
        "::: :::",
        "foo^2 bar~1",
        "{a, b, c}",
        "[123]",
        "\\ \\\\ \\",
        "<script>alert(1)</script>",
        "foo OR OR OR bar",
        "AND AND AND",
        "NOT",
    ]

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE doc_fts USING fts5("
        "title, headings, description, body, url_tokens, "
        "tokenize = 'porter unicode61 remove_diacritics 2')"
    )
    conn.execute(
        "INSERT INTO doc_fts (rowid, title, headings, description, body, url_tokens) "
        "VALUES (1, 'Test Doc', 'Heading', 'Desc', 'body text content', 'url')"
    )

    for hq in hostile_queries:
        parsed = parse_query(hq)
        if parsed.is_empty:
            continue
        and_expr = build_fts5_and_expression(parsed.terms_for_and())
        or_expr = build_fts5_or_expression(parsed.terms_for_or())

        # Neither AND nor OR expression should ever raise sqlite3.OperationalError syntax error
        if and_expr:
            cur = conn.cursor()
            cur.execute("SELECT rowid FROM doc_fts WHERE doc_fts MATCH ?", (and_expr,))
            _ = cur.fetchall()

        if or_expr:
            cur = conn.cursor()
            cur.execute("SELECT rowid FROM doc_fts WHERE doc_fts MATCH ?", (or_expr,))
            _ = cur.fetchall()

    conn.close()


@given(st.text(min_size=1, max_size=300))
@settings(max_examples=100)
def test_hypothesis_fuzz_fts5_query_safety(fuzz_text: str) -> None:
    """Hypothesis fuzz test proving that random Unicode input never causes FTS5 syntax errors."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE doc_fts USING fts5("
        "title, headings, description, body, url_tokens, "
        "tokenize = 'porter unicode61 remove_diacritics 2')"
    )
    conn.execute(
        "INSERT INTO doc_fts (rowid, title, headings, description, body, url_tokens) "
        "VALUES (1, 'Sample Title', 'Heading', 'Desc', 'Sample Body Text', 'example com')"
    )

    parsed = parse_query(fuzz_text)
    if not parsed.is_empty:
        and_expr = build_fts5_and_expression(parsed.terms_for_and())
        or_expr = build_fts5_or_expression(parsed.terms_for_or())

        if and_expr:
            cur = conn.cursor()
            cur.execute("SELECT rowid FROM doc_fts WHERE doc_fts MATCH ?", (and_expr,))
            _ = cur.fetchall()

        if or_expr:
            cur = conn.cursor()
            cur.execute("SELECT rowid FROM doc_fts WHERE doc_fts MATCH ?", (or_expr,))
            _ = cur.fetchall()

    conn.close()
