"""BM25-only SearchService implementing the core retrieval vertical slice (Decision D5)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from search_engine.indexer.fts import (
    DEFAULT_BM25_WEIGHTS,
    DEFAULT_TRI_WEIGHTS,
)
from search_engine.search.query import (
    MatchMode,
    build_fts5_and_expression,
    build_fts5_or_expression,
    build_fts5_trigram_expression,
    parse_query,
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    doc_id: int
    title: str
    url: str
    score: float
    doc_type: str
    lang: str
    description: str
    word_count: int
    published_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "url": self.url,
            "score": round(self.score, 4),
            "doc_type": self.doc_type,
            "lang": self.lang,
            "description": self.description,
            "word_count": self.word_count,
            "published_at": self.published_at,
        }


@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    normalized_query: str
    match_mode: str
    total_approx: int
    results: list[SearchResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "normalized_query": self.normalized_query,
            "match_mode": self.match_mode,
            "total_approx": self.total_approx,
            "results": [r.to_dict() for r in self.results],
        }


class SearchService:
    """Library search service executing BM25 retrieval over SQLite FTS5."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        doc_type: str | None = None,
        lang: str | None = None,
        weights: tuple[float, float, float, float, float] = DEFAULT_BM25_WEIGHTS,
    ) -> SearchResponse:
        """Execute BM25 search across doc_fts and doc_fts_tri."""
        parsed = parse_query(query)
        if parsed.is_empty:
            return SearchResponse(
                query=query,
                normalized_query="",
                match_mode=MatchMode.EMPTY.value,
                total_approx=0,
                results=[],
            )

        # 1. CJK query: route to trigram table
        if parsed.is_cjk:
            tri_expr = build_fts5_trigram_expression(parsed.terms_for_and())
            if not tri_expr:
                return SearchResponse(
                    query=query,
                    normalized_query=parsed.normalized_query,
                    match_mode=MatchMode.EMPTY.value,
                    total_approx=0,
                    results=[],
                )
            results, total = self._execute_fts_query(
                expr=tri_expr,
                table="doc_fts_tri",
                weights=DEFAULT_TRI_WEIGHTS,
                limit=limit,
                doc_type=doc_type,
                lang=lang,
            )
            return SearchResponse(
                query=query,
                normalized_query=parsed.normalized_query,
                match_mode=MatchMode.TRIGRAM.value,
                total_approx=total,
                results=results,
            )

        # 2. Standard query: try AND over all terms
        and_expr = build_fts5_and_expression(parsed.terms_for_and())
        results, total = self._execute_fts_query(
            expr=and_expr,
            table="doc_fts",
            weights=weights,
            limit=limit,
            doc_type=doc_type,
            lang=lang,
        )

        match_mode = MatchMode.AND.value

        # 3. If fewer than 5 results and multiple terms, retry with OR fallback per D5
        if len(results) < 5 and parsed.term_count > 1:
            or_terms = parsed.terms_for_or()
            or_expr = build_fts5_or_expression(or_terms)
            if or_expr:
                or_results, or_total = self._execute_fts_query(
                    expr=or_expr,
                    table="doc_fts",
                    weights=weights,
                    limit=limit,
                    doc_type=doc_type,
                    lang=lang,
                )
                if len(or_results) > len(results):
                    # Preserve AND matches at the top, then append unique OR matches
                    seen_ids = {r.doc_id for r in results}
                    for r in or_results:
                        if r.doc_id not in seen_ids:
                            results.append(r)
                            seen_ids.add(r.doc_id)
                        if len(results) >= limit:
                            break
                    match_mode = MatchMode.OR.value
                    total = max(total, or_total)

        return SearchResponse(
            query=query,
            normalized_query=parsed.normalized_query,
            match_mode=match_mode,
            total_approx=total,
            results=results[:limit],
        )

    def _execute_fts_query(
        self,
        *,
        expr: str,
        table: str,
        weights: tuple[float, ...],
        limit: int,
        doc_type: str | None,
        lang: str | None,
    ) -> tuple[list[SearchResult], int]:
        """Execute FTS5 MATCH with BM25 scoring and join document metadata."""
        cur = self._conn.cursor()
        w_str = ", ".join(str(float(w)) for w in weights)

        # Build filter clauses on documents table
        filter_clauses: list[str] = ["d.status = 'active'"]
        params: list[Any] = [expr]

        if doc_type:
            filter_clauses.append("d.doc_type = ?")
            params.append(doc_type)
        if lang:
            filter_clauses.append("d.lang = ?")
            params.append(lang)

        where_filter = " AND ".join(filter_clauses)
        params.append(limit)

        try:
            if table == "doc_fts":
                sql = f"""
                    SELECT
                        d.id AS doc_id,
                        d.title,
                        COALESCE(d.canonical_url, d.final_url, u.url_key, '') AS url,
                        bm25(doc_fts, {w_str}) AS score,
                        d.doc_type,
                        d.lang,
                        d.description,
                        d.word_count,
                        d.published_at
                    FROM doc_fts
                    JOIN documents d ON doc_fts.rowid = d.id
                    LEFT JOIN urls u ON d.url_id = u.id
                    WHERE doc_fts MATCH ? AND {where_filter}
                    ORDER BY score ASC
                    LIMIT ?
                """  # noqa: S608
            else:
                tri_weights = f"{float(weights[0])}, {float(weights[1])}"
                sql = f"""
                    SELECT
                        d.id AS doc_id,
                        d.title,
                        COALESCE(d.canonical_url, d.final_url, u.url_key, '') AS url,
                        bm25(doc_fts_tri, {tri_weights}) AS score,
                        d.doc_type,
                        d.lang,
                        d.description,
                        d.word_count,
                        d.published_at
                    FROM doc_fts_tri
                    JOIN documents d ON doc_fts_tri.rowid = d.id
                    LEFT JOIN urls u ON d.url_id = u.id
                    WHERE doc_fts_tri MATCH ? AND {where_filter}
                    ORDER BY score ASC
                    LIMIT ?
                """  # noqa: S608

            cur.execute(sql, tuple(params))
            rows = cur.fetchall()

            results = [
                SearchResult(
                    doc_id=int(r["doc_id"]),
                    title=str(r["title"] or ""),
                    url=str(r["url"] or ""),
                    score=float(r["score"]),
                    doc_type=str(r["doc_type"]),
                    lang=str(r["lang"]),
                    description=str(r["description"] or ""),
                    word_count=int(r["word_count"] or 0),
                    published_at=str(r["published_at"]) if r["published_at"] else None,
                )
                for r in rows
            ]
            return results, len(results)
        except sqlite3.OperationalError:
            return [], 0
