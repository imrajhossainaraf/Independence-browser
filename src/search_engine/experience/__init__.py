"""News, clusters, panels, synopsis, home, and suggestions (never imports net or crawler)."""

from __future__ import annotations

from search_engine.experience.clusters import (
    NewsCluster,
    cluster_news_items,
    compute_top_story_score,
    jaccard_similarity,
    title_shingles,
)
from search_engine.experience.home import HomeService
from search_engine.experience.models import (
    EntityCard,
    EntityFact,
    HomePayload,
    ImageRef,
    NewsItemSummary,
    NewsPagePayload,
    Panel,
    PreviewCard,
    RecentNewsItem,
    SourceHealthSummary,
    StorySourceRef,
    SuggestionItem,
    Synopsis,
    TopStoryItem,
)
from search_engine.experience.news import (
    NewsSourceConfig,
    NewsSourceHealth,
    apply_news_retention,
    clean_summary,
    get_sources_health,
    load_news_sources_file,
    parse_news_sources,
)
from search_engine.experience.panels import PanelService
from search_engine.experience.suggest import SuggestService, normalize_prefix, rebuild_suggestions
from search_engine.experience.synopsis import extract_synopsis

__all__ = [
    "EntityCard",
    "EntityFact",
    "HomePayload",
    "HomeService",
    "ImageRef",
    "NewsCluster",
    "NewsItemSummary",
    "NewsPagePayload",
    "NewsSourceConfig",
    "NewsSourceHealth",
    "Panel",
    "PanelService",
    "PreviewCard",
    "RecentNewsItem",
    "SourceHealthSummary",
    "StorySourceRef",
    "SuggestService",
    "SuggestionItem",
    "Synopsis",
    "TopStoryItem",
    "apply_news_retention",
    "clean_summary",
    "cluster_news_items",
    "compute_top_story_score",
    "extract_synopsis",
    "get_sources_health",
    "jaccard_similarity",
    "load_news_sources_file",
    "normalize_prefix",
    "parse_news_sources",
    "rebuild_suggestions",
    "title_shingles",
]
