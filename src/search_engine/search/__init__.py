"""Query processing and search services (never imports net or crawler)."""

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
from search_engine.search.service import (
    SearchResponse,
    SearchResult,
    SearchService,
)

__all__ = [
    "DEFAULT_STOPWORDS",
    "MAX_QUERY_LENGTH",
    "MAX_QUERY_TERMS",
    "MatchMode",
    "ProcessedQuery",
    "QueryToken",
    "SearchResponse",
    "SearchResult",
    "SearchService",
    "build_fts5_and_expression",
    "build_fts5_or_expression",
    "build_fts5_trigram_expression",
    "normalize_query_text",
    "parse_query",
    "quote_for_fts5",
]
