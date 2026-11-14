"""Pydantic models for experience layer: panels, synopsis, home, news (D19, D20)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ImageRef(BaseModel):
    """Reference to an opaque, approved local image (D15, D20)."""

    model_config = ConfigDict(frozen=True)

    id: str
    alt: str = ""
    width: int | None = None
    height: int | None = None


class EntityFact(BaseModel):
    """Key-value attribute for an entity knowledge card."""

    model_config = ConfigDict(frozen=True)

    label: str
    value: str


class EntityCard(BaseModel):
    """Sidebar knowledge card for an exact or high-confidence entity match."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["entity"] = "entity"
    title: str
    subtitle: str | None = None
    summary: str
    facts: list[EntityFact] = Field(default_factory=list)
    image: ImageRef | None = None
    source_name: str
    source_url: str
    license: str
    retrieved_at: str


class PreviewCard(BaseModel):
    """Sidebar fallback preview card for the top search result."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["preview"] = "preview"
    title: str
    domain: str
    url: str
    date: str | None = None
    lead: str
    thumbnail: ImageRef | None = None
    source_name: str


Panel = Annotated[EntityCard | PreviewCard, Field(discriminator="kind")]


class Synopsis(BaseModel):
    """Extractive, 100% grounded answer synopsis from a cited document (D19, Invariant 10)."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_name: str
    url: str
    fetched_at: str
    confidence: float


class RecentNewsItem(BaseModel):
    """Recent news story item from the last 7 days matching query."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    url: str
    source_name: str
    published_at: str
    category: str
    summary: str
    image: ImageRef | None = None


class StorySourceRef(BaseModel):
    """Source reference for a multi-source news cluster."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    published_at: str
    category: str


class TopStoryItem(BaseModel):
    """Clustered top story headline on the home page (D16, D20)."""

    model_config = ConfigDict(frozen=True)

    cluster_id: str
    headline: str
    sources: list[StorySourceRef] = Field(default_factory=list)
    summary: str
    published_at: str
    image: ImageRef | None = None


class NewsItemSummary(BaseModel):
    """Summary of an individual active news story."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    url: str
    source_name: str
    published_at: str
    category: str
    summary: str
    image: ImageRef | None = None


class SourceHealthSummary(BaseModel):
    """Health status summary of a configured news source."""

    model_config = ConfigDict(frozen=True)

    name: str
    feed_url_key: str
    category: str
    poll_interval_minutes: int
    last_success_at: str | None = None
    status: str
    consecutive_failures: int = 0
    is_healthy: bool = True
    is_stale: bool = False


class HomePayload(BaseModel):
    """Response payload for GET /api/v1/home (D20)."""

    model_config = ConfigDict(frozen=True)

    generated_at: str
    data_age_seconds: int
    is_stale: bool
    sources_health: list[SourceHealthSummary] = Field(default_factory=list)
    top_stories: list[TopStoryItem] = Field(default_factory=list)
    latest: list[NewsItemSummary] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class NewsPagePayload(BaseModel):
    """Paged news stories payload for GET /api/v1/news (D20)."""

    model_config = ConfigDict(frozen=True)

    items: list[NewsItemSummary] = Field(default_factory=list)
    total_count: int
    page: int
    page_size: int
    total_pages: int
    category: str | None = None


class SuggestionItem(BaseModel):
    """Prefix autocomplete suggestion item (Step 21)."""

    model_config = ConfigDict(frozen=True)

    text: str
    source_type: str
    priority: int
