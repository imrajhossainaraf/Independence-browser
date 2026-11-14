"""Query normalization, validation, safe token extraction, and FTS5 expression generation.

Decision D5:
- NFKC normalization, casefold, strip control characters, collapse whitespace.
- Limits: 256 characters, 32 terms.
- Quote every term for MATCH (internal quotes doubled); quoted phrases supported.
- AND-then-OR fallback: try AND over all terms; if fewer than 5 results, retry as OR
  after removing stopwords.
- Trigram routing for CJK / non-spaced queries.
- Hostile strings (unbalanced quotes, NEAR, column filters) must NEVER raise FTS syntax errors.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from search_engine.indexer.fts import is_cjk_text

MAX_QUERY_LENGTH: Final[int] = 256
MAX_QUERY_TERMS: Final[int] = 32

# Small built-in stopword list per D5
DEFAULT_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "he",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

_PHRASE_REGEX: Final[re.Pattern[str]] = re.compile(r'"([^"]*)"')


class MatchMode(StrEnum):
    EMPTY = "empty"
    AND = "and"
    OR = "or"
    TRIGRAM = "trigram"


@dataclass(frozen=True, slots=True)
class QueryToken:
    text: str
    is_phrase: bool = False


@dataclass(frozen=True, slots=True)
class ProcessedQuery:
    raw_query: str
    normalized_query: str
    tokens: list[QueryToken] = field(default_factory=list)
    is_cjk: bool = False

    @property
    def is_empty(self) -> bool:
        return len(self.tokens) == 0

    @property
    def term_count(self) -> int:
        return len(self.tokens)

    def terms_for_and(self) -> list[str]:
        """Return all terms/phrases for AND matching."""
        return [t.text for t in self.tokens]

    def terms_for_or(self, stopwords: frozenset[str] = DEFAULT_STOPWORDS) -> list[str]:
        """Return non-stopword terms for OR fallback."""
        or_terms: list[str] = []
        for t in self.tokens:
            if t.is_phrase:
                or_terms.append(t.text)
            else:
                word = t.text.strip().lower()
                if word not in stopwords and len(word) > 1:
                    or_terms.append(t.text)
        # If all terms were filtered out by stopwords, keep original terms
        if not or_terms:
            return [t.text for t in self.tokens]
        return or_terms


def normalize_query_text(query: str) -> str:
    """Normalize query text per D5: NFKC, casefold, strip control characters, collapse space."""
    if not query:
        return ""

    # 1. NFKC normalization and casefold
    normalized = unicodedata.normalize("NFKC", query).casefold()

    # 2. Strip control characters (category starting with 'C' like Cc, Cf, Cs, Co, Cn)
    # preserving newlines/tabs as space
    cleaned_chars = []
    for char in normalized:
        cat = unicodedata.category(char)
        if cat.startswith("C"):
            cleaned_chars.append(" ")
        else:
            cleaned_chars.append(char)
    cleaned = "".join(cleaned_chars)

    # 3. Collapse multiple whitespaces
    collapsed = " ".join(cleaned.split())

    # 4. Enforce 256 character limit
    return collapsed[:MAX_QUERY_LENGTH].strip()


def parse_query(raw_query: str) -> ProcessedQuery:
    """Parse a user query into normalized tokens, extracting quoted phrases safely."""
    norm = normalize_query_text(raw_query)
    if not norm:
        return ProcessedQuery(raw_query=raw_query, normalized_query="", tokens=[], is_cjk=False)

    is_cjk = is_cjk_text(norm)

    tokens: list[QueryToken] = []

    # Extract quoted phrases first
    # Find all "..." segments
    clean_norm = norm
    phrase_matches = list(_PHRASE_REGEX.finditer(clean_norm))

    last_idx = 0
    for match in phrase_matches:
        start, end = match.span()
        # Words before this phrase
        prefix = clean_norm[last_idx:start].strip()
        if prefix:
            for word in prefix.split():
                clean_word = word.strip('"').strip()
                if clean_word:
                    tokens.append(QueryToken(text=clean_word, is_phrase=False))

        phrase_content = match.group(1).strip()
        if phrase_content:
            # Collapse whitespace inside phrase
            clean_phrase = " ".join(phrase_content.split())
            if clean_phrase:
                tokens.append(QueryToken(text=clean_phrase, is_phrase=True))

        last_idx = end

    # Words after the last phrase
    suffix = clean_norm[last_idx:].strip()
    if suffix:
        for word in suffix.split():
            clean_word = word.strip('"').strip()
            if clean_word:
                tokens.append(QueryToken(text=clean_word, is_phrase=False))

    # Cap at MAX_QUERY_TERMS (32 terms)
    limited_tokens = tokens[:MAX_QUERY_TERMS]

    return ProcessedQuery(
        raw_query=raw_query,
        normalized_query=norm,
        tokens=limited_tokens,
        is_cjk=is_cjk,
    )


def quote_for_fts5(text: str) -> str:
    """Safely quote a token or phrase for SQLite FTS5 MATCH expressions.

    Doubles internal quotes and wraps in double quotes: '"' -> '""', 'foo' -> '"foo"'.
    Strips any characters or operators that could cause syntax errors.
    """
    clean = text.replace('"', '""')
    return f'"{clean}"'


def build_fts5_and_expression(tokens: list[str]) -> str:
    """Build an AND expression from terms for FTS5 MATCH."""
    if not tokens:
        return ""
    quoted = [quote_for_fts5(t) for t in tokens if t.strip()]
    if not quoted:
        return ""
    return " AND ".join(quoted)


def build_fts5_or_expression(tokens: list[str]) -> str:
    """Build an OR expression from terms for FTS5 MATCH."""
    if not tokens:
        return ""
    quoted = [quote_for_fts5(t) for t in tokens if t.strip()]
    if not quoted:
        return ""
    return " OR ".join(quoted)


def build_fts5_trigram_expression(tokens: list[str]) -> str:
    """Build a trigram expression for doc_fts_tri.

    Filters tokens to those with at least 3 characters (or falls back to combined tokens).
    Each valid token is quoted and joined with AND.
    """
    if not tokens:
        return ""
    valid_tokens = [t.strip() for t in tokens if len(t.strip()) >= 3]
    if not valid_tokens:
        combined = "".join("".join(tokens).split())
        if len(combined) < 3:
            return ""
        return quote_for_fts5(combined)
    quoted = [quote_for_fts5(t) for t in valid_tokens]
    return " AND ".join(quoted)
