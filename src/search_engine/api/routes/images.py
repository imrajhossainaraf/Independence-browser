"""Opaque image thumbnail serving route gated behind feature_images (D15, D17)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["images"])
ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/images/{image_id}")
def get_image(
    image_id: str,
    size: str = Query(default="160", description="Thumbnail size: 160 or 480"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.feature_images:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "not_found",
                "message": "Image serving is not enabled in this release",
            },
        )

    # 1. Validate image_id against path traversal attacks
    if not ID_REGEX.match(image_id):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_id", "message": "Malformed image ID"},
        )

    # 2. Validate size
    if size not in {"160", "480"}:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_size", "message": "Size must be 160 or 480"},
        )

    # 3. Look up image row in database
    with db.reader() as conn:
        row = conn.execute(
            "SELECT hash, status FROM images WHERE id = ? AND status = 'active'",
            (image_id,),
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Image not found"},
        )

    img_hash = str(row["hash"])
    file_path = Path(settings.image_dir) / img_hash[:2] / img_hash[2:4] / f"{img_hash}-{size}.webp"

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Image thumbnail file not found"},
        )

    try:
        content = file_path.read_bytes()
    except OSError as err:
        raise HTTPException(
            status_code=500,
            detail={"code": "storage_error", "message": f"Unable to read image file: {err}"},
        ) from err

    return Response(
        content=content,
        media_type="image/webp",
        headers={
            "Content-Type": "image/webp",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
