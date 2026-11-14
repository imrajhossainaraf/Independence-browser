"""HomeService composing home page data and news listings per D20."""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from typing import Any

from dateutil import parser as date_parser

from search_engine.config import Settings
from search_engine.experience.models import (
    HomePayload,
    ImageRef,
    NewsItemSummary,
    NewsPagePayload,
    SourceHealthSummary,
    StorySourceRef,
    TopStoryItem,
)
from search_engine.experience.news import get_sources_health


class HomeService:
    """Composes server-side data payloads for the home page and news sections."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def get_home(
        self,
        conn: sqlite3.Connection,
        *,
        now: datetime | None = None,
    ) -> HomePayload:
        """Compose the complete HomePayload for GET /api/v1/home (D20)."""
        if now is None:
            now = datetime.now(UTC)
        now_iso = now.isoformat()

        # 1. Sources health
        raw_health = get_sources_health(conn, now=now)
        sources_health: list[SourceHealthSummary] = [
            SourceHealthSummary(
                name=h.name,
                feed_url_key=h.feed_url_key,
                category=h.category,
                poll_interval_minutes=h.poll_interval_minutes,
                last_success_at=h.last_success_at,
                status="failing" if not h.is_healthy else ("stale" if h.is_stale else "healthy"),
                consecutive_failures=h.consecutive_failures,
                is_healthy=h.is_healthy,
                is_stale=h.is_stale,
            )
            for h in raw_health
        ]

        # 2. Data age calculation
        max_ts: datetime | None = None
        # Check newest news item
        r_news = conn.execute(
            "SELECT max(published_at), max(updated_at) FROM news_items WHERE status = 'active'"
        ).fetchone()
        if r_news:
            for val in (r_news[0], r_news[1]):
                if val:
                    try:
                        dt = date_parser.parse(str(val))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if max_ts is None or dt > max_ts:
                            max_ts = dt
                    except (ValueError, OverflowError):
                        pass

        # Check newest document
        r_doc = conn.execute(
            "SELECT max(fetched_at), max(updated_at) FROM documents WHERE status = 'active'"
        ).fetchone()
        if r_doc:
            for val in (r_doc[0], r_doc[1]):
                if val:
                    try:
                        dt = date_parser.parse(str(val))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=UTC)
                        if max_ts is None or dt > max_ts:
                            max_ts = dt
                    except (ValueError, OverflowError):
                        pass

        if max_ts is not None:
            data_age_seconds = max(0, int((now - max_ts.astimezone(UTC)).total_seconds()))
        else:
            data_age_seconds = 0

        # Stale evaluation: older than configured limit or all sources failing
        stale_limit_seconds = self.settings.news_stale_hours * 3600
        is_stale = (data_age_seconds > stale_limit_seconds) or (
            bool(sources_health) and all(not h.is_healthy for h in sources_health)
        )

        # 3. Top Stories (from clustered news table)
        top_stories: list[TopStoryItem] = []
        cluster_rows = conn.execute(
            """
            SELECT c.id, c.headline_item_id
            FROM news_clusters c
            ORDER BY c.updated_at DESC
            LIMIT 10
            """
        ).fetchall()

        for crow in cluster_rows:
            cluster_id = int(crow["id"])
            headline_id = str(crow["headline_item_id"])

            r_head = conn.execute(
                """
                SELECT n.id, n.title, n.summary, n.published_at, n.image_id, s.name as source_name
                FROM news_items n
                LEFT JOIN news_sources s ON n.source_id = s.id
                WHERE n.id = ?
                """,
                (headline_id,),
            ).fetchone()
            if not r_head:
                continue

            headline = str(r_head["title"] or "")
            summary = str(r_head["summary"] or "")
            pub_at = str(r_head["published_at"] or "")
            img_id = r_head["image_id"]

            img: ImageRef | None = None
            if img_id and self.settings.news_images_enabled:
                img = ImageRef(id=str(img_id), alt=headline)

            source_rows = conn.execute(
                """
                SELECT s.name as source_name, n.link as url, n.published_at, n.category
                FROM news_items n
                LEFT JOIN news_sources s ON n.source_id = s.id
                WHERE n.cluster_id = ?
                ORDER BY n.published_at DESC
                """,
                (cluster_id,),
            ).fetchall()

            sources_refs = [
                StorySourceRef(
                    name=str(sr["source_name"] or "News"),
                    url=str(sr["url"]),
                    published_at=str(sr["published_at"]),
                    category=str(sr["category"] or "General"),
                )
                for sr in source_rows
            ]

            top_stories.append(
                TopStoryItem(
                    cluster_id=str(cluster_id),
                    headline=headline,
                    sources=sources_refs,
                    summary=summary,
                    published_at=pub_at,
                    image=img,
                )
            )

        # 4. Latest stories (20 newest active items)
        rows_latest = conn.execute(
            """
            SELECT n.id, n.title, n.link as url, n.summary, n.published_at, n.category, n.image_id,
                   s.name as source_name
            FROM news_items n
            LEFT JOIN news_sources s ON n.source_id = s.id
            WHERE n.status = 'active'
            ORDER BY n.published_at DESC
            LIMIT 20
            """
        ).fetchall()

        latest: list[NewsItemSummary] = []
        for r in rows_latest:
            img_ref: ImageRef | None = None
            if r["image_id"] and self.settings.news_images_enabled:
                img_ref = ImageRef(id=str(r["image_id"]), alt=str(r["title"]))

            latest.append(
                NewsItemSummary(
                    id=str(r["id"]),
                    title=str(r["title"]),
                    url=str(r["url"]),
                    source_name=str(r["source_name"] or "News"),
                    published_at=str(r["published_at"]),
                    category=str(r["category"] or "General"),
                    summary=str(r["summary"] or ""),
                    image=img_ref,
                )
            )

        # 5. Distinct categories
        rows_cat = conn.execute(
            """
            SELECT DISTINCT category
            FROM news_items
            WHERE status = 'active' AND category IS NOT NULL AND trim(category) != ''
            ORDER BY category ASC
            """
        ).fetchall()
        categories = [str(r[0]) for r in rows_cat]

        return HomePayload(
            generated_at=now_iso,
            data_age_seconds=data_age_seconds,
            is_stale=is_stale,
            sources_health=sources_health,
            top_stories=top_stories,
            latest=latest,
            categories=categories,
        )

    def get_news_page(
        self,
        conn: sqlite3.Connection,
        *,
        category: str | None = None,
        page: int = 1,
        page_size: int = 20,
        now: datetime | None = None,
    ) -> NewsPagePayload:
        """Query paged news stories with optional category filtering (D20)."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        if category and category.strip():
            count_sql = (
                "SELECT count(*) FROM news_items n WHERE n.status = 'active' AND n.category = ?"
            )
            query_sql = """
                SELECT n.id, n.title, n.link as url, n.summary, n.published_at,
                       n.category, n.image_id, s.name as source_name
                FROM news_items n
                LEFT JOIN news_sources s ON n.source_id = s.id
                WHERE n.status = 'active' AND n.category = ?
                ORDER BY n.published_at DESC
                LIMIT ? OFFSET ?
            """
            count_params: list[Any] = [category.strip()]
            query_params: list[Any] = [category.strip(), page_size, offset]
        else:
            count_sql = "SELECT count(*) FROM news_items n WHERE n.status = 'active'"
            query_sql = """
                SELECT n.id, n.title, n.link as url, n.summary, n.published_at,
                       n.category, n.image_id, s.name as source_name
                FROM news_items n
                LEFT JOIN news_sources s ON n.source_id = s.id
                WHERE n.status = 'active'
                ORDER BY n.published_at DESC
                LIMIT ? OFFSET ?
            """
            count_params = []
            query_params = [page_size, offset]

        total_count = int(conn.execute(count_sql, count_params).fetchone()[0])
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1

        rows = conn.execute(query_sql, query_params).fetchall()

        items: list[NewsItemSummary] = []
        for r in rows:
            img_ref: ImageRef | None = None
            if r["image_id"] and self.settings.news_images_enabled:
                img_ref = ImageRef(id=str(r["image_id"]), alt=str(r["title"]))

            items.append(
                NewsItemSummary(
                    id=str(r["id"]),
                    title=str(r["title"]),
                    url=str(r["url"]),
                    source_name=str(r["source_name"] or "News"),
                    published_at=str(r["published_at"]),
                    category=str(r["category"] or "General"),
                    summary=str(r["summary"] or ""),
                    image=img_ref,
                )
            )

        return NewsPagePayload(
            items=items,
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            category=category,
        )
