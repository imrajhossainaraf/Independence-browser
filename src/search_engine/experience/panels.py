"""PanelService assembling Entity Card, Preview Card, Synopsis, and Recent News per D19."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from search_engine.config import Settings
from search_engine.experience.models import (
    EntityCard,
    ImageRef,
    Panel,
    PreviewCard,
    RecentNewsItem,
    Synopsis,
)
from search_engine.experience.synopsis import _extract_content_terms, extract_synopsis
from search_engine.indexer import decompress_text

_WORD_PATTERN = re.compile(r"\b[a-zA-Z0-9_\u0080-\uffff]+\b")


class PanelService:
    """Service that generates sidebar panels, synopsis, and recent news for results."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def assemble(
        self,
        conn: sqlite3.Connection,
        query: str,
        results: list[Any],
        *,
        now: datetime | None = None,
    ) -> tuple[Panel | None, Synopsis | None, list[RecentNewsItem] | None]:
        """Assemble (panel, synopsis, recent_news) for a query and search results.

        Order of execution per D19:
        1. Entity card if entity matches (Release B; returns None in Release A).
        2. Otherwise Preview card for top result group.
        3. Answer synopsis (extractive, 100% verbatim grounded).
        4. Recent news (up to 3 items from past 7 days matching query; None if empty).
        """
        if now is None:
            now = datetime.now(UTC)

        # 1. Entity card (gated by feature_entities in Release B)
        panel: Panel | None = None
        if self.settings.feature_entities:
            panel = self._find_entity_card(conn, query)

        # 2. Preview card fallback for top result
        if panel is None and results:
            panel = self._build_preview_card(conn, results[0])

        # 3. Answer synopsis
        synopsis: Synopsis | None = extract_synopsis(conn, query, results, self.settings)

        # 4. Recent news block
        recent_news = self._find_recent_news(conn, query, now=now)

        return panel, synopsis, recent_news

    def _find_entity_card(self, conn: sqlite3.Connection, query: str) -> EntityCard | None:
        """Find entity card match (Release B, placeholder when flag is off)."""
        # When feature_entities is on in Release B, EntityMatcher queries here
        return None

    def _build_preview_card(self, conn: sqlite3.Connection, top_result: Any) -> PreviewCard | None:
        """Build sidebar preview card for the top search result."""
        url = getattr(top_result, "url", "")
        if not url:
            return None

        title = getattr(top_result, "title", "")
        snippet = getattr(top_result, "snippet", "")
        doc_id = getattr(top_result, "doc_id", None)
        published_at = getattr(top_result, "published_at", None)

        parsed = urlsplit(url)
        domain = parsed.netloc or ""

        # Extract clean lead text
        lead = snippet
        if doc_id is not None:
            row = conn.execute(
                "SELECT text_gz, published_at FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            if row is not None:
                content_text = decompress_text(row["text_gz"]) if row["text_gz"] else ""
                if row["published_at"]:
                    published_at = str(row["published_at"])
                if content_text:
                    paragraphs = [p.strip() for p in content_text.split("\n\n") if p.strip()]
                    if paragraphs:
                        lead = paragraphs[0]

        max_lead = self.settings.preview_lead_chars
        if len(lead) > max_lead:
            # Cut at word boundary
            cut = lead[:max_lead]
            last_space = cut.rfind(" ")
            lead = (cut[:last_space] if last_space > 50 else cut).rstrip() + "..."

        source_name = domain.removeprefix("www.")

        return PreviewCard(
            kind="preview",
            title=title or domain,
            domain=domain,
            url=url,
            date=published_at,
            lead=lead,
            thumbnail=None,
            source_name=source_name,
        )

    def _find_recent_news(
        self,
        conn: sqlite3.Connection,
        query: str,
        *,
        now: datetime,
    ) -> list[RecentNewsItem] | None:
        """Find up to 3 news stories from the past 7 days matching the query."""
        terms = _extract_content_terms(query)
        if not terms:
            return None

        cutoff_iso = (now - timedelta(days=self.settings.recent_news_days)).isoformat()

        # Query active news items from last 7 days
        rows = conn.execute(
            """
            SELECT n.id, n.title, n.link as url, n.summary, n.published_at, n.category, n.image_id,
                   s.name as source_name
            FROM news_items n
            LEFT JOIN news_sources s ON n.source_id = s.id
            WHERE n.status = 'active' AND n.published_at >= ?
            ORDER BY n.published_at DESC
            LIMIT 50
            """,
            (cutoff_iso,),
        ).fetchall()

        if not rows:
            return None

        scored: list[tuple[float, RecentNewsItem]] = []
        for r in rows:
            title = str(r["title"] or "")
            summary = str(r["summary"] or "")
            text_lower = f"{title} {summary}".lower()
            tokens = set(_WORD_PATTERN.findall(text_lower))

            matched = [t for t in terms if t in tokens]
            if not matched:
                continue

            # Title matches have high weight
            title_tokens = set(_WORD_PATTERN.findall(title.lower()))
            title_matches = sum(1 for t in matched if t in title_tokens)

            relevance = len(matched) * 1.0 + title_matches * 2.0

            img_ref: ImageRef | None = None
            if r["image_id"] and self.settings.news_images_enabled:
                img_ref = ImageRef(id=str(r["image_id"]), alt=title)

            item = RecentNewsItem(
                id=str(r["id"]),
                title=title,
                url=str(r["url"]),
                source_name=str(r["source_name"] or "News"),
                published_at=str(r["published_at"]),
                category=str(r["category"] or "General"),
                summary=summary,
                image=img_ref,
            )
            scored.append((relevance, item))

        if not scored:
            return None

        scored.sort(key=lambda s: s[0], reverse=True)
        top_items = [s[1] for s in scored[: self.settings.recent_news_max]]
        return top_items if top_items else None
