"""Prefix autocomplete suggestions route gated behind feature_autocomplete (D15, D20)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from search_engine.api.models import SuggestResponse
from search_engine.experience.suggest import SuggestService

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["suggest"])


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/suggest", response_model=SuggestResponse)
def get_suggestions(
    q: str = Query(..., min_length=2, max_length=100, description="Prefix to autocomplete"),
    limit: int = Query(default=8, ge=1, le=20, description="Maximum suggestions to return"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SuggestResponse:
    if not settings.feature_autocomplete:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Autocomplete endpoint is not enabled in this release",
            },
        )

    suggest_svc = SuggestService(settings)
    with db.reader() as conn:
        suggestions = suggest_svc.suggest(conn, prefix=q, limit=limit)

    return SuggestResponse(query=q, suggestions=suggestions)
