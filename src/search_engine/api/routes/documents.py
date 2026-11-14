"""Document metadata route (Step 22)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request

from search_engine.api.models import DocumentMetadataResponse

if TYPE_CHECKING:
    from search_engine.config import Settings
    from search_engine.db import Database

router = APIRouter(tags=["documents"])


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/documents/{doc_id}", response_model=DocumentMetadataResponse)
def get_document_metadata(
    doc_id: int,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> DocumentMetadataResponse:
    with db.reader() as conn:
        row = conn.execute(
            """
            SELECT id, final_url, title, doc_type, published_at, word_count, status
            FROM documents
            WHERE id = ? AND status = 'active'
            """,
            (doc_id,),
        ).fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Document not found"},
        )

    return DocumentMetadataResponse(
        id=int(row["id"]),
        url=str(row["final_url"]),
        title=str(row["title"] or ""),
        doc_type=str(row["doc_type"] or "page"),
        published_at=str(row["published_at"]) if row["published_at"] else None,
        word_count=int(row["word_count"] or 0),
    )
