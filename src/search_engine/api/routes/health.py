"""Health check route reporting system and component status without leaking paths (D15)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from search_engine import __version__
from search_engine.api.models import DataAges, HealthResponse
from search_engine.indexer.fts import INDEX_VERSION

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["health"])


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/health", response_model=HealthResponse)
def health_check(
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    components: dict[str, str] = {}
    data_ages = DataAges()
    schema_version = "0000"
    overall_status = "healthy"

    try:
        with db.reader() as conn:
            # 1. Database check
            conn.execute("SELECT 1").fetchone()
            components["database"] = "healthy"

            # 2. Schema version check
            try:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_version"
                ).fetchone()
                if row and row[0] is not None:
                    schema_version = f"{int(row[0]):04d}"
            except sqlite3.OperationalError:
                schema_version = "unmigrated"
                components["database"] = "unmigrated"
                overall_status = "degraded"

            # 3. Index check
            try:
                conn.execute("SELECT count(*) FROM doc_fts").fetchone()
                components["index"] = "healthy"
            except sqlite3.OperationalError:
                components["index"] = "unavailable"
                overall_status = "degraded"

            # 4. News check and data ages
            try:
                # Latest crawl time
                crawl_row = conn.execute(
                    "SELECT max(fetched_at) FROM documents WHERE status = 'active'"
                ).fetchone()
                if crawl_row and crawl_row[0]:
                    data_ages.last_crawl_at = str(crawl_row[0])

                # Latest poll time and unhealthy sources
                poll_row = conn.execute("SELECT max(last_success_at) FROM news_sources").fetchone()
                if poll_row and poll_row[0]:
                    data_ages.last_poll_at = str(poll_row[0])

                failing_sources = conn.execute(
                    "SELECT count(*) FROM news_sources WHERE consecutive_failures >= 3"
                ).fetchone()
                if failing_sources and failing_sources[0] > 0:
                    components["news"] = "degraded"
                    if overall_status == "healthy":
                        overall_status = "degraded"
                else:
                    components["news"] = "healthy"

                # Newest news item age
                newest_item = conn.execute(
                    "SELECT max(published_at) FROM news_items WHERE status = 'active'"
                ).fetchone()
                if newest_item and newest_item[0]:
                    try:
                        pub_dt = datetime.fromisoformat(str(newest_item[0]))
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=UTC)
                        now_utc = datetime.now(UTC)
                        age = int((now_utc - pub_dt).total_seconds())
                        data_ages.newest_item_age_seconds = max(0, age)
                    except ValueError:
                        pass
            except sqlite3.OperationalError:
                components["news"] = "unavailable"

    except Exception:
        components["database"] = "unhealthy"
        overall_status = "error"

    payload = {
        "status": overall_status,
        "api_version": __version__,
        "schema_version": schema_version,
        "index_version": INDEX_VERSION,
        "data_ages": data_ages.model_dump(),
        "components": components,
    }
    status_code = 200 if overall_status != "error" else 503
    return JSONResponse(status_code=status_code, content=payload)
