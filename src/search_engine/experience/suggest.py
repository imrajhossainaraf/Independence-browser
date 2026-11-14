"""Prefix autocomplete SuggestService per Step 21."""

from __future__ import annotations

import sqlite3
import unicodedata

from search_engine.config import Settings


def normalize_prefix(text: str) -> str:
    """Normalize text for prefix autocomplete: lowercase and strip diacritics/accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_clean = "".join(c for c in decomposed if not unicodedata.combining(c))
    return ascii_clean.lower().strip()


def rebuild_suggestions(conn: sqlite3.Connection, *, feature_entities: bool = False) -> int:
    """Rebuild the prefix-optimized suggestions table from active index content (Step 21).

    Priority:
    1. Entity names and aliases (Release B; only if feature_entities=True)
    2. Active news headlines (priority 2)
    3. Active document titles (priority 3)
    4. Vocabulary terms with cnt >= 2 (priority 4)

    Deduplicated by text; higher-priority source wins.
    """
    conn.execute("DELETE FROM suggestions")

    inserted = 0
    sql_insert = """
        INSERT INTO suggestions (text, normalized_prefix, source_type, priority)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (text, normalized_prefix) DO UPDATE SET
            priority = MIN(suggestions.priority, excluded.priority),
            source_type = CASE
                WHEN excluded.priority < suggestions.priority THEN excluded.source_type
                ELSE suggestions.source_type
            END
    """

    # 1. Entity names (when enabled)
    if feature_entities:
        pass  # Reserved for Release B entities store

    # 2. Active news headlines (priority 2)
    rows_headlines = conn.execute(
        "SELECT DISTINCT title FROM news_items WHERE status = 'active' AND trim(title) != ''"
    ).fetchall()
    for r in rows_headlines:
        title = str(r[0]).strip()
        norm = normalize_prefix(title)
        if len(norm) >= 2:
            conn.execute(sql_insert, (title, norm, "headline", 2))
            inserted += 1
        words = title.split()
        if len(words) > 1:
            for i in range(1, len(words)):
                suffix = " ".join(words[i:])
                norm_sub = normalize_prefix(suffix)
                if len(norm_sub) >= 2:
                    conn.execute(sql_insert, (title, norm_sub, "headline", 2))
                    inserted += 1

    # 3. Active document titles (priority 3)
    rows_titles = conn.execute(
        "SELECT DISTINCT title FROM documents WHERE status = 'active' AND trim(title) != ''"
    ).fetchall()
    for r in rows_titles:
        title = str(r[0]).strip()
        norm = normalize_prefix(title)
        if len(norm) >= 2:
            conn.execute(sql_insert, (title, norm, "title", 3))
            inserted += 1
        words = title.split()
        if len(words) > 1:
            for i in range(1, len(words)):
                suffix = " ".join(words[i:])
                norm_sub = normalize_prefix(suffix)
                if len(norm_sub) >= 2:
                    conn.execute(sql_insert, (title, norm_sub, "title", 3))
                    inserted += 1

    # 4. Vocabulary terms with count >= 2 (priority 4)
    try:
        rows_terms = conn.execute(
            "SELECT term FROM doc_fts_vocab WHERE cnt >= 2 AND length(term) >= 3"
        ).fetchall()
        for r in rows_terms:
            term = str(r[0]).strip()
            norm = normalize_prefix(term)
            if len(norm) >= 2:
                conn.execute(sql_insert, (term, norm, "term", 4))
                inserted += 1
    except sqlite3.OperationalError:
        # If doc_fts_vocab table is not initialized yet in this conn
        pass

    return inserted


class SuggestService:
    """Prefix autocomplete service returning up to 8 plain-text suggestions."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def suggest(
        self,
        conn: sqlite3.Connection,
        prefix: str,
        limit: int = 8,
    ) -> list[str]:
        """Return up to limit plain-text suggestions for a prefix >= 2 characters.

        Ranked by source priority (entity, headline, title, term) then alphabetically.
        Returns empty list if prefix has fewer than 2 characters.
        """
        cleaned = prefix.strip()
        if len(cleaned) < 2:
            return []

        norm_prefix = normalize_prefix(cleaned)
        if len(norm_prefix) < 2:
            return []

        limit = max(1, min(20, limit))

        # Query suggestions by normalized prefix
        # (grouped by text to prevent duplicates when multiple words match)
        rows = conn.execute(
            """
            SELECT text
            FROM suggestions
            WHERE normalized_prefix LIKE ? || '%'
            GROUP BY text
            ORDER BY MIN(priority) ASC, text ASC
            LIMIT ?
            """,
            (norm_prefix, limit),
        ).fetchall()

        return [str(r[0]) for r in rows]
