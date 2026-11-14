"""SQLite FTS5 index build, querying, and consistency checks."""

from search_engine.indexer.build import (
    IndexBuildReport,
    deactivate_document,
    get_index_version,
    rebuild_index,
    remove_document_index,
    set_index_version,
    upsert_document_index,
)
from search_engine.indexer.consistency import ConsistencyReport, verify_index_consistency
from search_engine.indexer.fts import (
    DEFAULT_BM25_WEIGHTS,
    DEFAULT_TRI_WEIGHTS,
    INDEX_VERSION,
    MAX_TRI_PREFIX_CHARS,
    compress_text,
    decompress_text,
    is_cjk_text,
    search_bm25,
    tokenize_url,
)

__all__ = [
    "DEFAULT_BM25_WEIGHTS",
    "DEFAULT_TRI_WEIGHTS",
    "INDEX_VERSION",
    "MAX_TRI_PREFIX_CHARS",
    "ConsistencyReport",
    "IndexBuildReport",
    "compress_text",
    "deactivate_document",
    "decompress_text",
    "get_index_version",
    "is_cjk_text",
    "rebuild_index",
    "remove_document_index",
    "search_bm25",
    "set_index_version",
    "tokenize_url",
    "upsert_document_index",
    "verify_index_consistency",
]
