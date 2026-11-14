"""FTS5 full-text search definitions, query execution, and text helpers.

Decision D4:
- Main table doc_fts with 'porter unicode61 remove_diacritics 2'.
- Trigram table doc_fts_tri with 'trigram' on (title, body_prefix) up to 5,000 chars.
- Weighted bm25() with defaults: title 10, headings 5, description 3, body 1, url_tokens 1.
"""

from __future__ import annotations

import gzip
import re
import sqlite3
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlparse

INDEX_VERSION: Final[int] = 1
DEFAULT_BM25_WEIGHTS: Final[tuple[float, float, float, float, float]] = (10.0, 5.0, 3.0, 1.0, 1.0)
DEFAULT_TRI_WEIGHTS: Final[tuple[float, float]] = (10.0, 1.0)
MAX_TRI_PREFIX_CHARS: Final[int] = 5000

_CJK_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
_TOKEN_SPLIT: Final[re.Pattern[str]] = re.compile(r"[^a-zA-Z0-9]+")


@dataclass(frozen=True, slots=True)
class FtsDocument:
    doc_id: int
    title: str
    headings: str
    description: str
    body: str
    url_tokens: str
    doc_type: str
    lang: str


def is_cjk_text(text: str) -> bool:
    """True if text contains Chinese, Japanese, or Korean characters."""
    return bool(_CJK_PATTERN.search(text))


def tokenize_url(url: str | None) -> str:
    """Extract alphanumeric keyword tokens from URL hostname and path."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        path = unquote(parsed.path)
        netloc = parsed.netloc
        combined = f"{netloc} {path}"
    except Exception:
        combined = str(url)

    tokens = [t for t in _TOKEN_SPLIT.split(combined) if t and len(t) > 1]
    return " ".join(tokens).lower()


def decompress_text(text_gz: bytes | None) -> str:
    """Decompress gzip-encoded text bytes. Fails closed to empty string."""
    if not text_gz:
        return ""
    try:
        return gzip.decompress(text_gz).decode("utf-8", errors="replace")
    except Exception:
        return ""


def compress_text(text: str) -> bytes:
    """Compress text to gzip bytes."""
    if not text:
        return b""
    return gzip.compress(text.encode("utf-8"))


def quote_fts5_term(term: str) -> str:
    """Safely quote a term for SQLite FTS5 MATCH queries."""
    clean = term.replace('"', '""')
    return f'"{clean}"'


def search_bm25(
    conn: sqlite3.Connection,
    query: str,
    *,
    weights: tuple[float, float, float, float, float] = DEFAULT_BM25_WEIGHTS,
    table: str = "doc_fts",
    limit: int = 20,
) -> list[tuple[int, float]]:
    """Search using weighted bm25() score. Returns list of (doc_id, score).

    Lower (more negative) score indicates a better match in SQLite FTS5.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    is_cjk = is_cjk_text(clean_query)
    target_table = "doc_fts_tri" if (is_cjk and table == "doc_fts") else table

    # In trigram table for CJK, strip spaces between characters
    if is_cjk and target_table == "doc_fts_tri":
        terms = [clean_query.replace(" ", "")]
    else:
        terms = [t for t in clean_query.split() if t]

    if not terms:
        return []

    quoted_terms = [quote_fts5_term(t) for t in terms]
    and_expr = " AND ".join(quoted_terms)

    cur = conn.cursor()
    w_str = ", ".join(str(float(w)) for w in weights)

    try:
        if target_table == "doc_fts":
            sql = f"""
                SELECT rowid, bm25(doc_fts, {w_str}) AS score
                FROM doc_fts
                WHERE doc_fts MATCH ?
                ORDER BY score ASC
                LIMIT ?
            """  # noqa: S608
            cur.execute(sql, (and_expr, limit))
            rows = cur.fetchall()
        else:
            tri_weights = f"{float(weights[0])}, {float(weights[3])}"
            sql = f"""
                SELECT rowid, bm25(doc_fts_tri, {tri_weights}) AS score
                FROM doc_fts_tri
                WHERE doc_fts_tri MATCH ?
                ORDER BY score ASC
                LIMIT ?
            """  # noqa: S608
            cur.execute(sql, (and_expr, limit))
            rows = cur.fetchall()

        results = [(int(r[0]), float(r[1])) for r in rows]

        # If AND returns fewer than 3 results and there were multiple terms, supplement with OR
        if len(results) < 3 and len(quoted_terms) > 1:
            or_expr = " OR ".join(quoted_terms)
            seen_ids = {doc_id for doc_id, _ in results}
            if target_table == "doc_fts":
                sql = f"""
                    SELECT rowid, bm25(doc_fts, {w_str}) AS score
                    FROM doc_fts
                    WHERE doc_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                """  # noqa: S608
                cur.execute(sql, (or_expr, limit))
                or_rows = cur.fetchall()
            else:
                tri_weights = f"{float(weights[0])}, {float(weights[3])}"
                sql = f"""
                    SELECT rowid, bm25(doc_fts_tri, {tri_weights}) AS score
                    FROM doc_fts_tri
                    WHERE doc_fts_tri MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                """  # noqa: S608
                cur.execute(sql, (or_expr, limit))
                or_rows = cur.fetchall()

            for r in or_rows:
                doc_id = int(r[0])
                score = float(r[1])
                if doc_id not in seen_ids:
                    results.append((doc_id, score))
                    seen_ids.add(doc_id)
                if len(results) >= limit:
                    break

        return results[:limit]
    except sqlite3.OperationalError:
        return []
