"""Unit tests for Step 14: Safe query-biased snippets and sentence splitting.

Tests:
- Matching terms appear in HighlightSegment with is_match=True
- Snippet length is bounded
- Page HTML such as script tags and event handlers stays plain text (XSS safety)
- Fallback snippet when no query terms match body
- Multilingual text: CJK sentence boundaries, French accents, German umlauts
- Very long documents handle scoring efficiently
- Metadata-only documents return clear metadata-only label
- Deterministic output across multiple executions
- Sentence splitter on abbreviations, decimals, initials, and lists
"""

from __future__ import annotations

from search_engine.search.snippets import (
    HighlightSegment,
    extract_snippet,
    highlight_text,
    split_sentences,
)

# ---------------------------------------------------------------------------
# 1. Sentence Splitter Tests
# ---------------------------------------------------------------------------


def test_split_sentences_basic_and_whitespace() -> None:
    text = "First sentence. Second sentence! Third sentence? Fourth sentence."
    sents = split_sentences(text)
    assert sents == [
        "First sentence.",
        "Second sentence!",
        "Third sentence?",
        "Fourth sentence.",
    ]
    assert split_sentences("") == []
    assert split_sentences("   \n\t  ") == []


def test_split_sentences_abbreviations_and_initials() -> None:
    text = (
        "Dr. Smith met with Prof. Jones at 5 p.m. to discuss the U.S. and U.K. treaty. "
        "Then they left."
    )
    sents = split_sentences(text)
    assert len(sents) == 2
    assert (
        sents[0] == "Dr. Smith met with Prof. Jones at 5 p.m. to discuss the U.S. and U.K. treaty."
    )
    assert sents[1] == "Then they left."

    # e.g. and i.e.
    text_eg = "Use an inverted index, e.g. SQLite FTS5, for fast queries. It scales well."
    sents_eg = split_sentences(text_eg)
    assert len(sents_eg) == 2
    assert "e.g. SQLite FTS5" in sents_eg[0]
    assert sents_eg[1] == "It scales well."


def test_split_sentences_decimals_and_version_numbers() -> None:
    text = "Pi is approximately 3.14159 in geometry. The project released v0.1.0 today."
    sents = split_sentences(text)
    assert len(sents) == 2
    assert "3.14159" in sents[0]
    assert "v0.1.0" in sents[1]


def test_split_sentences_cjk_punctuation() -> None:
    text = "北京气候峰会在中国召开。探讨可再生能源转型！各国代表达成共识？新的目标已确立。"
    sents = split_sentences(text)
    assert len(sents) == 4
    assert sents[0] == "北京气候峰会在中国召开。"
    assert sents[1] == "探讨可再生能源转型！"
    assert sents[2] == "各国代表达成共识？"
    assert sents[3] == "新的目标已确立。"


# ---------------------------------------------------------------------------
# 2. Highlighting & Structured Segments (XSS Invariant)
# ---------------------------------------------------------------------------


def test_highlight_text_matches_and_preserves_case() -> None:
    text = "Python is a versatile programming language. Learn python today!"
    segments = highlight_text(text, ["python"])

    matched_texts = [s.text for s in segments if s.is_match]
    # Preserves exact original casing
    assert matched_texts == ["Python", "python"]
    assert all(s.is_match for s in segments if s.text.lower() == "python")

    # Reconstructed text exactly equals original text
    assert "".join(s.text for s in segments) == text


def test_highlight_text_xss_stays_plain_text() -> None:
    # Hostile HTML with script tag, iframe, and event handler
    hostile = "<script>alert('xss')</script> and <img src=x onerror=alert(1)> in Python code."
    segments = highlight_text(hostile, ["alert", "python"])

    # Must be structured segments with literal text, no HTML tags introduced
    for s in segments:
        assert isinstance(s, HighlightSegment)
        assert "<mark>" not in s.text
        assert "<b>" not in s.text
        assert "</em>" not in s.text

    # Literal script tags remain untouched text
    assert "".join(s.text for s in segments) == hostile
    matched = [s.text for s in segments if s.is_match]
    assert "alert" in matched
    assert "Python" in matched


def test_highlight_accents_and_umlauts() -> None:
    # Query 'cafe' matches 'café'
    text_fr = "Venez déguster un excellent café au cœur de Paris."
    segs_fr = highlight_text(text_fr, ["cafe"])
    matched_fr = [s.text for s in segs_fr if s.is_match]
    assert "café" in matched_fr

    # Query 'munchen' matches 'München'
    text_de = "Besuchen Sie München für Kultur und bayerische Gastfreundschaft."
    segs_de = highlight_text(text_de, ["munchen"])
    matched_de = [s.text for s in segs_de if s.is_match]
    assert "München" in matched_de


# ---------------------------------------------------------------------------
# 3. Best-Passage Snippet Extraction
# ---------------------------------------------------------------------------


def test_extract_snippet_bounds_length_and_selects_best_passage() -> None:
    body = (
        "Introduction to database concepts. "
        "Relational databases use B-Trees for primary indexes. "
        "In contrast, full-text search engines use inverted indexes to map terms to documents. "
        "This inverted index architecture provides sub-millisecond retrieval on large corpora. "
        "Conclusion and summary of storage benchmarks."
    )
    query = "inverted index retrieval"
    snippet = extract_snippet(body, query, max_chars=160)

    # Reconstructed snippet string
    full_str = "".join(s.text for s in snippet)
    assert len(full_str) <= 180  # bounded within sensible boundary margin
    assert any(s.is_match and "inverted" in s.text.lower() for s in snippet)
    assert any(s.is_match and "index" in s.text.lower() for s in snippet)
    assert any(s.is_match and "retrieval" in s.text.lower() for s in snippet)


def test_extract_snippet_joins_two_passages_with_ellipsis() -> None:
    body = (
        "First section discusses distributed systems theory. "
        "The Raft consensus algorithm guarantees safety across network partitions. "
        "Middle unrelated text about hardware servers and networking protocols. "
        "Later section evaluates Paxos vs Raft consensus algorithms in practice. "
        "Final remarks on cloud infrastructure."
    )
    query = "Raft consensus"
    snippet = extract_snippet(body, query, max_chars=200)

    full_str = "".join(s.text for s in snippet)
    # Both passages selected and joined by ' ... '
    assert " ... " in full_str
    assert "guarantees safety" in full_str
    assert "Paxos vs Raft" in full_str


def test_extract_snippet_fallback_when_no_match() -> None:
    body = "General overview of cooking recipes. Fresh ingredients make a great meal."
    desc = "Cooking guide and culinary techniques."
    query = "quantum physics"

    snippet = extract_snippet(body, query, description=desc, max_chars=150)
    full_str = "".join(s.text for s in snippet)
    # Falls back to description
    assert "Cooking guide" in full_str
    assert not any(s.is_match for s in snippet)


def test_extract_snippet_metadata_only_label() -> None:
    snippet_meta = extract_snippet(None, "test query", is_metadata_only=True, description="")
    full_str = "".join(s.text for s in snippet_meta)
    assert "Metadata only" in full_str

    # With description on metadata-only document
    snippet_meta_desc = extract_snippet(
        "", "test query", is_metadata_only=True, description="Overview of the dataset."
    )
    assert "".join(s.text for s in snippet_meta_desc) == "Overview of the dataset."


def test_extract_snippet_deterministic_and_very_long_document() -> None:
    # 150 sentences
    long_body = " ".join(
        f"Sentence {i} describes background details in section {i // 10}."
        if i != 75
        else (
            "Sentence 75 reveals the breakthrough discovery in quantum "
            "computing with superconducting qubits."
        )
        for i in range(150)
    )
    query = "breakthrough discovery quantum computing"

    snip1 = extract_snippet(long_body, query, max_chars=180)
    snip2 = extract_snippet(long_body, query, max_chars=180)

    # Identical across runs
    assert [s.to_dict() for s in snip1] == [s.to_dict() for s in snip2]

    full_str = "".join(s.text for s in snip1)
    assert "breakthrough discovery" in full_str
    assert "quantum computing" in full_str
    assert len(full_str) <= 190
