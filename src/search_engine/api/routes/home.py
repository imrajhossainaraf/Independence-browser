"""Home page composition route with 60-second caching and ETag support (D15, D20)."""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from starlette.responses import JSONResponse

from search_engine.experience.home import HomeService
from search_engine.experience.models import HomePayload

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["home"])


class HomeCache:
    """In-memory cache for HomePayload with 60s TTL and ETag."""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self.ttl_seconds = ttl_seconds
        self.cached_time: float = 0.0
        self.etag: str = ""
        self.payload_dict: dict[str, Any] = {}

    def get(self, now: float | None = None) -> tuple[str, dict[str, Any]] | None:
        t = now if now is not None else time.monotonic()
        if self.etag and (t - self.cached_time) < self.ttl_seconds:
            return self.etag, self.payload_dict
        return None

    def set(self, payload: HomePayload, now: float | None = None) -> str:
        t = now if now is not None else time.monotonic()
        data = payload.model_dump(mode="json")
        body_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
        etag = f'"{hashlib.sha256(body_bytes).hexdigest()[:16]}"'
        self.cached_time = t
        self.etag = etag
        self.payload_dict = data
        return etag

    def invalidate(self) -> None:
        self.cached_time = 0.0
        self.etag = ""
        self.payload_dict = {}


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_home_cache(request: Request) -> HomeCache:
    if not hasattr(request.app.state, "home_cache"):
        request.app.state.home_cache = HomeCache()
    return request.app.state.home_cache  # type: ignore[no-any-return]


@router.get("/home", response_model=HomePayload)
def get_home(
    request: Request,
    if_none_match: str | None = Header(default=None),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    cache: HomeCache = Depends(get_home_cache),
) -> Response:
    # 1. Check cache
    cached = cache.get()
    if cached is not None:
        etag, payload_dict = cached
        if if_none_match and if_none_match.strip() == etag:
            return Response(
                status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=60"}
            )
        return JSONResponse(
            content=payload_dict,
            headers={"ETag": etag, "Cache-Control": "public, max-age=60"},
        )

    # 2. Compute payload from read-only connection
    home_svc = HomeService(settings)
    with db.reader() as conn:
        payload = home_svc.get_home(conn)

    # 3. Cache payload
    etag = cache.set(payload)

    if if_none_match and if_none_match.strip() == etag:
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=60"}
        )

    return JSONResponse(
        content=cache.payload_dict,
        headers={"ETag": etag, "Cache-Control": "public, max-age=60"},
    )
