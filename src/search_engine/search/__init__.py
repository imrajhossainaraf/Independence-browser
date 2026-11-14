"""Query processing and search services (never imports net or crawler)."""

from search_engine.search.grouping import (
    GroupedDocument,
    extract_host,
    group_and_paginate,
    normalize_group_key,
)
from search_engine.search.query import (
    DEFAULT_STOPWORDS,
    MAX_QUERY_LENGTH,
    MAX_QUERY_TERMS,
    MatchMode,
    ProcessedQuery,
    QueryToken,
    build_fts5_and_expression,
    build_fts5_or_expression,
    build_fts5_trigram_expression,
    normalize_query_text,
    parse_query,
    quote_for_fts5,
)
from search_engine.search.ranking import (
    RankingConfig,
    ScoreExplanation,
    compute_freshness_multiplier,
    compute_phrase_multiplier,
    compute_quality_multiplier,
    compute_score,
    compute_title_multiplier,
)
from search_engine.search.service import (
    SearchResponse,
    SearchResult,
    SearchService,
)
from search_engine.search.suggest import (
    SpellingResult,
    load_vocab_terms,
    suggest_spelling,
)

__all__ = [
    "DEFAULT_STOPWORDS",
    "GroupedDocument",
    "MAX_QUERY_LENGTH",
    "MAX_QUERY_TERMS",
    "MatchMode",
    "ProcessedQuery",
    "QueryToken",
    "RankingConfig",
    "ScoreExplanation",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "SpellingResult",
    "build_fts5_and_expression",
    "build_fts5_or_expression",
    "build_fts5_trigram_expression",
    "compute_freshness_multiplier",
    "compute_phrase_multiplier",
    "compute_quality_multiplier",
    "compute_score",
    "compute_title_multiplier",
    "extract_host",
    "group_and_paginate",
    "load_vocab_terms",
    "normalize_group_key",
    "normalize_query_text",
    "parse_query",
    "quote_for_fts5",
    "suggest_spelling",
]
