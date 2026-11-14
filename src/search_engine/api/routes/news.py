"""Paged news route for topic navigation and archives (D20)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request

from search_engine.experience.home import HomeService
from search_engine.experience.models import NewsPagePayload

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["news"])


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/news", response_model=NewsPagePayload)
def get_news(
    category: str | None = Query(default=None, description="Filter by news category"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> NewsPagePayload:
    home_svc = HomeService(settings)
    with db.reader() as conn:
        return home_svc.get_news_page(conn, category=category, page=page, page_size=page_size)
