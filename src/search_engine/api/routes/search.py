"""Search route providing full-text search, synopsis, panels, and recent news (D15, D19)."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from search_engine.search.service import SearchService

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["search"])
logger = logging.getLogger("search_engine.api.search")


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/search")
def search(
    request: Request,
    q: str | None = Query(default=None, description="Search query string"),
    query: str | None = Query(default=None, description="Alias for q"),
    page: int = Query(default=1, ge=1, le=100, description="Page number"),
    page_size: int = Query(default=10, ge=1, le=50, description="Results per page"),
    type: str | None = Query(default=None, description="Filter by document type"),
    lang: str | None = Query(default=None, alias="language", description="Filter by language"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    query_str = (q or query or "").strip()

    if len(query_str) > 256:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "query_too_long",
                "message": "Query exceeds maximum length of 256 characters",
            },
        )

    # Execute search against read-only database connection
    page_num = max(1, page)
    offset = (page_num - 1) * page_size
    with db.reader() as conn:
        # Check index availability per Section 7A Graceful Degradation
        try:
            conn.execute("SELECT 1 FROM doc_fts LIMIT 1").fetchone()
        except sqlite3.OperationalError as err:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "index_unavailable",
                    "message": ("Search index is unavailable. Run 'search-engine index rebuild'."),
                },
            ) from err

        service = SearchService(conn, settings=settings)
        resp = service.search(
            query=query_str,
            limit=page_size,
            offset=offset,
            doc_type=type,
            lang=lang,
        )

    # Safe structured logging per D15 (query text redacted unless log_query_text enabled)
    client_id = getattr(request.state, "request_id", "unknown")
    log_data: dict[str, Any] = {
        "request_id": client_id,
        "query_length": len(query_str),
        "total_approx": resp.total_approx,
        "match_mode": resp.match_mode,
        "results_count": len(resp.results),
        "has_synopsis": resp.synopsis is not None,
        "has_panel": resp.panel is not None,
    }
    if settings.log_query_text:
        log_data["query"] = query_str
    logger.info("Search executed", extra=log_data)

    return resp.to_dict()
