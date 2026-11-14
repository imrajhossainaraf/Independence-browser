"""Extractive grounded answer synopsis service per D19 and Invariant 10."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from search_engine.config import Settings
from search_engine.experience.models import Synopsis
from search_engine.indexer import decompress_text
from search_engine.search.snippets import split_sentences

_WORD_PATTERN = re.compile(r"\b[a-zA-Z0-9_\u0080-\uffff]+\b")
_STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "aren't",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can't",
    "cannot",
    "could",
    "couldn't",
    "did",
    "didn't",
    "do",
    "does",
    "doesn't",
    "doing",
    "don't",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "hadn't",
    "has",
    "hasn't",
    "have",
    "haven't",
    "having",
    "he",
    "he'd",
    "he'll",
    "he's",
    "her",
    "here",
    "here's",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "how's",
    "i",
    "i'd",
    "i'll",
    "i'm",
    "i've",
    "if",
    "in",
    "into",
    "is",
    "isn't",
    "it",
    "it's",
    "its",
    "itself",
    "let's",
    "me",
    "more",
    "most",
    "mustn't",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "ought",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "shan't",
    "she",
    "she'd",
    "she'll",
    "she's",
    "should",
    "shouldn't",
    "so",
    "some",
    "such",
    "than",
    "that",
    "that's",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "there's",
    "these",
    "they",
    "they'd",
    "they'll",
    "they're",
    "they've",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "wasn't",
    "we",
    "we'd",
    "we'll",
    "we're",
    "we've",
    "were",
    "weren't",
    "what",
    "what's",
    "when",
    "when's",
    "where",
    "where's",
    "which",
    "while",
    "who",
    "who's",
    "whom",
    "why",
    "why's",
    "with",
    "won't",
    "would",
    "wouldn't",
    "you",
    "you'd",
    "you'll",
    "you're",
    "you've",
    "your",
    "yours",
    "yourself",
    "yourselves",
}


def _extract_content_terms(query: str) -> list[str]:
    """Extract content terms from query, filtering out common stopwords."""
    words = [w.lower() for w in _WORD_PATTERN.findall(query)]
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def _is_navigation_or_list_line(sentence: str) -> bool:
    """Detect navigation, table of contents, or list item text."""
    stripped = sentence.strip()
    if not stripped:
        return True
    # Markdown list items, bullets, headers
    if stripped.startswith(("-", "*", "#", "|", ">", "•", "→")):
        return True
    # Check if sentence has letters
    if not re.search(r"[a-zA-Z\u0080-\uffff]", stripped):
        return True
    # Fewer than 4 words
    tokens = _WORD_PATTERN.findall(stripped)
    if len(tokens) < 4:
        return True
    # Boilerplate check
    low = stripped.lower()
    if any(
        bp in low
        for bp in (
            "all rights reserved",
            "cookie policy",
            "privacy policy",
            "terms of service",
            "terms of use",
            "sign in",
            "log in",
            "create account",
        )
    ):
        return True
    return False


@dataclass(frozen=True)
class _CandidateSentence:
    text: str
    source_name: str
    url: str
    fetched_at: str
    score: float
    coverage: float


def extract_synopsis(
    conn: sqlite3.Connection,
    query: str,
    top_results: list[Any],
    settings: Settings,
) -> Synopsis | None:
    """Extract an answer synopsis verbatim from one of the top 5 documents per D19.

    Guarantees:
    - Query must have at least 2 content terms.
    - Text is 100% a verbatim substring of the cited stored document.
    - Low coverage or document score below threshold yields None.
    """
    content_terms = _extract_content_terms(query)
    # D19: "query with at least 2 content terms"
    if len(content_terms) < 2:
        return None

    if not top_results:
        return None

    min_doc_score = settings.synopsis_min_doc_score
    min_coverage = settings.synopsis_min_coverage
    min_chars = settings.synopsis_min_chars
    max_chars = settings.synopsis_max_chars

    candidates: list[_CandidateSentence] = []

    # Inspect top 5 result groups
    for result in top_results[:5]:
        score = getattr(result, "score", 0.0)
        if score < min_doc_score:
            continue

        doc_id = getattr(result, "doc_id", None)
        if doc_id is None:
            continue

        row = conn.execute(
            (
                "SELECT title, final_url, text_gz, fetched_at "
                "FROM documents WHERE id = ? AND status = 'active'"
            ),
            (doc_id,),
        ).fetchone()
        if row is None:
            continue

        title = str(row["title"] or "")
        url = str(row["final_url"] or "")
        content_text = decompress_text(row["text_gz"])
        fetched_at = str(row["fetched_at"] or "")

        if not content_text.strip():
            continue

        sentences = split_sentences(content_text)
        n_sent = len(sentences)

        for i, sent in enumerate(sentences):
            if _is_navigation_or_list_line(sent):
                continue

            # Candidate 1: Single sentence
            cand_variants = [sent]

            # Candidate 2: Optional pair of consecutive sentences if single is short
            if len(sent) < 150 and i + 1 < n_sent:
                next_sent = sentences[i + 1]
                if not _is_navigation_or_list_line(next_sent):
                    combined = f"{sent} {next_sent}"
                    if combined in content_text and len(combined) <= max_chars:
                        cand_variants.append(combined)

            for cand in cand_variants:
                if len(cand) < min_chars or len(cand) > max_chars:
                    continue

                # 100% Verbatim Substring Check
                if cand not in content_text:
                    continue

                cand_lower = cand.lower()
                cand_tokens = set(_WORD_PATTERN.findall(cand_lower))

                matched = [t for t in content_terms if t in cand_tokens]
                coverage = len(matched) / len(content_terms)

                if coverage < min_coverage:
                    continue

                # Compute sentence score (coverage * BM25-like term match weight)
                match_weight = sum(1 for t in matched if t in cand_tokens)
                length_penalty = 1.0 - abs(len(cand) - 160) / 400.0
                cand_score = coverage * (1.0 + match_weight * 0.5) * max(0.5, length_penalty)

                # Bonus for clean sentence capitalization and punctuation
                if cand[0].isupper() and cand[-1] in (".", "!", "?"):
                    cand_score *= 1.2

                candidates.append(
                    _CandidateSentence(
                        text=cand,
                        source_name=title or url.split("/")[2] if "/" in url else url,
                        url=url,
                        fetched_at=fetched_at,
                        score=cand_score,
                        coverage=coverage,
                    )
                )

    if not candidates:
        return None

    # Pick the highest scoring candidate
    candidates.sort(key=lambda c: (c.score, c.coverage), reverse=True)
    best = candidates[0]

    # Map score to normalized confidence [0.0, 1.0]
    confidence = min(1.0, max(0.0, best.score / 2.5))

    return Synopsis(
        text=best.text,
        source_name=best.source_name,
        url=best.url,
        fetched_at=best.fetched_at,
        confidence=round(confidence, 3),
    )
