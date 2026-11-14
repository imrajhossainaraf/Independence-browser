"""Pydantic models for the HTTP API (Step 22)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Standard error detail per D15."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable error code string")
    message: str = Field(description="Human-readable error explanation")


class ErrorResponse(BaseModel):
    """Standard single error response shape per D15: {error: {code, message}}."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


class DataAges(BaseModel):
    """Timestamp summary for health status."""

    model_config = ConfigDict(extra="forbid")

    last_crawl_at: str | None = None
    last_poll_at: str | None = None
    newest_item_age_seconds: int | None = None


class HealthResponse(BaseModel):
    """GET /api/v1/health response model."""

    model_config = ConfigDict(extra="forbid")

    status: str = "healthy"
    api_version: str = "0.1.0"
    schema_version: str = "0004"
    index_version: int = 1
    data_ages: DataAges = Field(default_factory=DataAges)
    components: dict[str, str] = Field(default_factory=dict)


class SuggestResponse(BaseModel):
    """GET /api/v1/suggest response model."""

    model_config = ConfigDict(extra="forbid")

    query: str
    suggestions: list[str]


class DocumentMetadataResponse(BaseModel):
    """GET /api/v1/documents/{id} response model."""

    model_config = ConfigDict(extra="forbid")

    id: int
    url: str
    title: str
    doc_type: str
    published_at: str | None = None
    word_count: int = 0
