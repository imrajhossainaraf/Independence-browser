"""Did-you-mean spelling suggestions using fts5vocab and rapidfuzz (Decision D5).

Rules implemented:
- Vocabulary loaded from doc_fts_vocab virtual table
- rapidfuzz DamerauLevenshtein distance matching
- Distance at most 2 for terms >= 5 chars, at most 1 for terms < 5 chars
- Transposition and single-character typo correction
- No suggestion when results are already good
- Auto-correction banner only when zero results and exactly one candidate at edit distance 1 exists
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz.distance import DamerauLevenshtein

_SPLIT_WORDS_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class SpellingResult:
    suggested_query: str
    is_auto_corrected: bool
    original_query: str
    corrections: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggested_query": self.suggested_query,
            "is_auto_corrected": self.is_auto_corrected,
            "original_query": self.original_query,
            "corrections": self.corrections,
        }


def load_vocab_terms(conn: sqlite3.Connection) -> dict[str, int]:
    """Load indexed terms and frequencies from doc_fts_vocab."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT term, cnt FROM doc_fts_vocab WHERE cnt >= 1 ORDER BY cnt DESC LIMIT 20000"
        )
        return {str(r[0]).lower(): int(r[1]) for r in cur.fetchall()}
    except sqlite3.OperationalError:
        return {}


def suggest_spelling(
    conn: sqlite3.Connection,
    query: str,
    *,
    result_count: int = 0,
    top_score: float = 0.0,
    vocab: dict[str, int] | None = None,
) -> SpellingResult | None:
    """Generate spelling suggestions from fts5vocab using rapidfuzz."""
    # 1. No suggestions when results are good (e.g. multiple results with solid score)
    if result_count >= 3 and top_score >= 1.0:
        return None

    terms = _SPLIT_WORDS_RE.findall(query.lower())
    if not terms:
        return None

    vocabulary = vocab if vocab is not None else load_vocab_terms(conn)
    if not vocabulary:
        return None

    corrections: dict[str, str] = {}
    distance_1_candidates_count = 0
    single_token_dist1 = False

    for term in terms:
        # If term exists in vocabulary, it's correct
        if term in vocabulary:
            continue

        max_dist = 1 if len(term) < 5 else 2

        # Find candidates within allowed edit distance
        matching_candidates: list[tuple[int, int, str]] = []  # (dist, -cnt, candidate)
        dist_1_count_for_term = 0

        for cand, cnt in vocabulary.items():
            if abs(len(cand) - len(term)) > max_dist:
                continue
            dist = int(DamerauLevenshtein.distance(term, cand))
            if dist <= max_dist:
                matching_candidates.append((dist, -cnt, cand))
                if dist == 1:
                    dist_1_count_for_term += 1

        if not matching_candidates:
            # Term has no close match, keep original
            continue

        matching_candidates.sort()
        best_match = matching_candidates[0][2]
        corrections[term] = best_match

        if len(terms) == 1 and dist_1_count_for_term == 1:
            single_token_dist1 = True
            distance_1_candidates_count = 1

    if not corrections:
        return None

    # Build suggested query preserving casing/format where possible
    suggested_tokens: list[str] = []
    for t in terms:
        suggested_tokens.append(corrections.get(t, t))
    suggested_query = " ".join(suggested_tokens)

    if suggested_query.lower() == query.strip().lower():
        return None

    # Auto-correction rule per Decision D5:
    # Only when a query returns zero results and exactly one candidate at edit distance 1 exists
    is_auto_corrected = (
        result_count == 0
        and len(terms) == 1
        and single_token_dist1
        and distance_1_candidates_count == 1
    )

    return SpellingResult(
        suggested_query=suggested_query,
        is_auto_corrected=is_auto_corrected,
        original_query=query,
        corrections=corrections,
    )
