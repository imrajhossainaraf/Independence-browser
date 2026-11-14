"""SearchService with ranking, grouping, host diversity, and suggestions (D5, D6, D7)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from search_engine.config import Settings
from search_engine.indexer import decompress_text

if TYPE_CHECKING:
    from search_engine.experience.models import Panel, RecentNewsItem, Synopsis
    from search_engine.experience.panels import PanelService
from search_engine.indexer.fts import (
    DEFAULT_BM25_WEIGHTS,
    DEFAULT_TRI_WEIGHTS,
)
from search_engine.search.grouping import (
    GroupedDocument,
    extract_host,
    group_and_paginate,
)
from search_engine.search.query import (
    MatchMode,
    build_fts5_and_expression,
    build_fts5_or_expression,
    build_fts5_trigram_expression,
    parse_query,
)
from search_engine.search.ranking import (
    RankingConfig,
    ScoreExplanation,
    compute_score,
)
from search_engine.search.snippets import (
    HighlightSegment,
    extract_snippet,
    highlight_text,
)
from search_engine.search.suggest import (
    load_vocab_terms,
    suggest_spelling,
)

CANDIDATE_POOL_SIZE: int = 200
_WORD_BOUNDARY_RE = re.compile(r"\b\w+\b", re.UNICODE)


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
    canonical_url: str | None = None
    content_hash: str | None = None
    host: str = ""
    duplicate_count: int = 1
    score_explanation: ScoreExplanation | None = None
    title_highlight: list[HighlightSegment] = field(default_factory=list)
    snippet: list[HighlightSegment] = field(default_factory=list)

    @property
    def snippet_text(self) -> str:
        return "".join(s.text for s in self.snippet)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "doc_id": self.doc_id,
            "title": self.title,
            "url": self.url,
            "score": round(self.score, 4),
            "doc_type": self.doc_type,
            "lang": self.lang,
            "description": self.description,
            "word_count": self.word_count,
            "published_at": self.published_at,
            "host": self.host,
            "duplicate_count": self.duplicate_count,
            "title_highlight": [s.to_dict() for s in self.title_highlight],
            "snippet": [s.to_dict() for s in self.snippet],
            "snippet_text": self.snippet_text,
        }
        if self.score_explanation:
            data["score_explanation"] = self.score_explanation.to_dict()
        return data


@dataclass(frozen=True, slots=True)
class SearchResponse:
    query: str
    normalized_query: str
    match_mode: str
    total_approx: int
    results: list[SearchResult] = field(default_factory=list)
    suggestion: str | None = None
    is_auto_corrected: bool = False
    original_query: str | None = None
    page: int = 1
    page_size: int = 10
    total_groups: int = 0
    ranking_config: RankingConfig | None = None
    panel: Panel | None = None
    synopsis: Synopsis | None = None
    recent_news: list[RecentNewsItem] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "query": self.query,
            "normalized_query": self.normalized_query,
            "match_mode": self.match_mode,
            "total_approx": self.total_approx,
            "total_groups": self.total_groups,
            "page": self.page,
            "page_size": self.page_size,
            "results": [r.to_dict() for r in self.results],
            "panel": self.panel.model_dump() if self.panel is not None else None,
            "synopsis": self.synopsis.model_dump() if self.synopsis is not None else None,
            "recent_news": (
                [n.model_dump() for n in self.recent_news] if self.recent_news is not None else None
            ),
        }
        if self.suggestion:
            data["suggestion"] = self.suggestion
            data["is_auto_corrected"] = self.is_auto_corrected
        if self.original_query:
            data["original_query"] = self.original_query
        return data


class SearchService:
    """Search service executing BM25 retrieval, explainable ranking, grouping, and suggestions."""

    def __init__(self, conn: sqlite3.Connection, settings: Settings | None = None):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._settings = settings
        if settings is not None:
            from search_engine.experience.panels import PanelService

            self._panel_service: PanelService | None = PanelService(settings)
        else:
            self._panel_service = None
        self._vocab: dict[str, int] | None = None

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        doc_type: str | None = None,
        lang: str | None = None,
        ranking_config: RankingConfig | None = None,
        weights: tuple[float, float, float, float, float] = DEFAULT_BM25_WEIGHTS,
        reference_time: datetime | None = None,
        auto_correct_depth: int = 0,
    ) -> SearchResponse:
        """Execute full search pipeline: parse -> retrieve -> rank -> group -> paginate."""
        config = ranking_config or RankingConfig()
        parsed = parse_query(query)

        if parsed.is_empty:
            return SearchResponse(
                query=query,
                normalized_query="",
                match_mode=MatchMode.EMPTY.value,
                total_approx=0,
                results=[],
                page_size=limit,
                ranking_config=config,
            )

        candidate_limit = max(CANDIDATE_POOL_SIZE, (offset + limit) * 2)

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
                    page_size=limit,
                    ranking_config=config,
                )
            candidates, total_candidates = self._fetch_candidates(
                expr=tri_expr,
                table="doc_fts_tri",
                weights=DEFAULT_TRI_WEIGHTS,
                candidate_limit=candidate_limit,
                doc_type=doc_type,
                lang=lang,
            )
            match_mode = MatchMode.TRIGRAM.value
        else:
            # 2. Standard query: try AND over all terms
            and_expr = build_fts5_and_expression(parsed.terms_for_and())
            candidates, total_candidates = self._fetch_candidates(
                expr=and_expr,
                table="doc_fts",
                weights=weights,
                candidate_limit=candidate_limit,
                doc_type=doc_type,
                lang=lang,
            )
            match_mode = MatchMode.AND.value

            # 3. If fewer than 5 results and multiple terms, retry with OR fallback per D5
            if len(candidates) < 5 and parsed.term_count > 1:
                or_terms = parsed.terms_for_or()
                or_expr = build_fts5_or_expression(or_terms)
                if or_expr:
                    or_candidates, or_total = self._fetch_candidates(
                        expr=or_expr,
                        table="doc_fts",
                        weights=weights,
                        candidate_limit=candidate_limit,
                        doc_type=doc_type,
                        lang=lang,
                    )
                    if len(or_candidates) > len(candidates):
                        seen_ids = {c["doc_id"] for c in candidates}
                        for oc in or_candidates:
                            if oc["doc_id"] not in seen_ids:
                                candidates.append(oc)
                                seen_ids.add(oc["doc_id"])
                            if len(candidates) >= candidate_limit:
                                break
                        match_mode = MatchMode.OR.value
                        total_candidates = max(total_candidates, or_total)

        # 4. If zero results, check for auto-correction or spelling suggestion
        if not candidates:
            if auto_correct_depth == 0:
                if self._vocab is None:
                    self._vocab = load_vocab_terms(self._conn)
                spelling = suggest_spelling(
                    self._conn,
                    query,
                    result_count=0,
                    vocab=self._vocab,
                )
                if spelling and spelling.is_auto_corrected:
                    # Execute search for corrected query with banner
                    corrected_resp = self.search(
                        spelling.suggested_query,
                        limit=limit,
                        offset=offset,
                        doc_type=doc_type,
                        lang=lang,
                        ranking_config=config,
                        weights=weights,
                        reference_time=reference_time,
                        auto_correct_depth=auto_correct_depth + 1,
                    )
                    return SearchResponse(
                        query=spelling.suggested_query,
                        normalized_query=corrected_resp.normalized_query,
                        match_mode=corrected_resp.match_mode,
                        total_approx=corrected_resp.total_approx,
                        results=corrected_resp.results,
                        suggestion=spelling.suggested_query,
                        is_auto_corrected=True,
                        original_query=query,
                        page=(offset // limit) + 1,
                        page_size=limit,
                        total_groups=corrected_resp.total_groups,
                        ranking_config=config,
                        panel=corrected_resp.panel,
                        synopsis=corrected_resp.synopsis,
                        recent_news=corrected_resp.recent_news,
                    )
                if spelling:
                    return SearchResponse(
                        query=query,
                        normalized_query=parsed.normalized_query,
                        match_mode=MatchMode.EMPTY.value,
                        total_approx=0,
                        results=[],
                        suggestion=spelling.suggested_query,
                        page_size=limit,
                        ranking_config=config,
                    )

            return SearchResponse(
                query=query,
                normalized_query=parsed.normalized_query,
                match_mode=MatchMode.EMPTY.value,
                total_approx=0,
                results=[],
                page_size=limit,
                ranking_config=config,
            )

        # 5. Score candidates using ranking signals (Decision D6)
        scored_docs: list[GroupedDocument] = []
        for c in candidates:
            final_score, explanation = compute_score(
                c["raw_bm25"],
                query_str=parsed.normalized_query,
                title=c["title"],
                url=c["url"],
                description=c["description"],
                doc_type=c["doc_type"],
                word_count=c["word_count"],
                published_at=c["published_at"],
                config=config,
                reference_time=reference_time,
            )
            host = c["host"] or extract_host(c["url"])
            scored_docs.append(
                GroupedDocument(
                    doc_id=c["doc_id"],
                    title=c["title"],
                    url=c["url"],
                    score=final_score,
                    doc_type=c["doc_type"],
                    lang=c["lang"],
                    description=c["description"],
                    word_count=c["word_count"],
                    published_at=c["published_at"],
                    canonical_url=c["canonical_url"],
                    canonical_group_id=c.get("canonical_group_id"),
                    content_hash=c["content_hash"],
                    host=host,
                    score_explanation=explanation,
                )
            )

        # 6. Group by canonical URL/hash, apply host diversity in top 10, paginate (Decision D7)
        paged_groups, total_groups = group_and_paginate(
            scored_docs,
            offset=offset,
            limit=limit,
            enable_host_diversity=config.enable_host_diversity,
        )

        # 7. Fetch stored text to generate safe query-biased snippets and title highlights
        doc_ids = [g.doc_id for g in paged_groups]
        doc_text_map: dict[int, tuple[str | None, str]] = {}
        if doc_ids:
            placeholders = ",".join("?" for _ in doc_ids)
            cur = self._conn.cursor()
            cur.execute(
                f"SELECT id, text_gz, status FROM documents WHERE id IN ({placeholders})",  # noqa: S608
                tuple(doc_ids),
            )
            for r in cur.fetchall():
                did = int(r["id"])
                gz_blob = r["text_gz"]
                status_val = str(r["status"])
                raw_text = decompress_text(gz_blob) if gz_blob else None
                doc_text_map[did] = (raw_text, status_val)

        query_tokens = _WORD_BOUNDARY_RE.findall(parsed.normalized_query)

        results: list[SearchResult] = []
        for g in paged_groups:
            body_text, status_val = doc_text_map.get(g.doc_id, (None, "active"))
            is_meta = status_val == "metadata_only"
            snippet_segs = extract_snippet(
                body_text,
                parsed.normalized_query,
                title=g.title,
                description=g.description,
                is_metadata_only=is_meta,
            )
            title_segs = highlight_text(g.title, query_tokens)

            results.append(
                SearchResult(
                    doc_id=g.doc_id,
                    title=g.title,
                    url=g.url,
                    score=g.score,
                    doc_type=g.doc_type,
                    lang=g.lang,
                    description=g.description,
                    word_count=g.word_count,
                    published_at=g.published_at,
                    canonical_url=g.canonical_url,
                    content_hash=g.content_hash,
                    host=g.host,
                    duplicate_count=g.duplicate_count,
                    score_explanation=g.score_explanation,
                    title_highlight=title_segs,
                    snippet=snippet_segs,
                )
            )

        # 8. Check for did-you-mean suggestion when results are not already solid
        suggestion: str | None = None
        if auto_correct_depth == 0:
            if self._vocab is None:
                self._vocab = load_vocab_terms(self._conn)
            top_score = results[0].score if results else 0.0
            sp = suggest_spelling(
                self._conn,
                query,
                result_count=len(results),
                top_score=top_score,
                vocab=self._vocab,
            )
            if sp:
                suggestion = sp.suggested_query

        panel: Panel | None = None
        synopsis: Synopsis | None = None
        recent_news: list[RecentNewsItem] | None = None
        if self._panel_service is not None and auto_correct_depth == 0:
            panel, synopsis, recent_news = self._panel_service.assemble(
                self._conn,
                query,
                results,
                now=reference_time,
            )

        page_num = (offset // limit) + 1
        return SearchResponse(
            query=query,
            normalized_query=parsed.normalized_query,
            match_mode=match_mode,
            total_approx=total_groups,
            results=results,
            suggestion=suggestion,
            is_auto_corrected=False,
            original_query=None,
            page=page_num,
            page_size=limit,
            total_groups=total_groups,
            ranking_config=config,
            panel=panel,
            synopsis=synopsis,
            recent_news=recent_news,
        )

    def _fetch_candidates(
        self,
        *,
        expr: str,
        table: str,
        weights: tuple[float, ...],
        candidate_limit: int,
        doc_type: str | None,
        lang: str | None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch raw candidate rows with BM25 score from FTS5."""
        cur = self._conn.cursor()
        w_str = ", ".join(str(float(w)) for w in weights)

        filter_clauses: list[str] = ["d.status = 'active'"]
        params: list[Any] = [expr]

        if doc_type:
            filter_clauses.append("d.doc_type = ?")
            params.append(doc_type)
        if lang:
            filter_clauses.append("d.lang = ?")
            params.append(lang)

        where_filter = " AND ".join(filter_clauses)
        params.append(candidate_limit)

        try:
            if table == "doc_fts":
                sql = f"""
                    SELECT
                        d.id AS doc_id,
                        d.title,
                        COALESCE(d.canonical_url, d.final_url, u.url_key, '') AS url,
                        d.canonical_url,
                        d.canonical_group_id,
                        d.content_hash,
                        h.host,
                        bm25(doc_fts, {w_str}) AS raw_bm25,
                        d.doc_type,
                        d.lang,
                        d.description,
                        d.word_count,
                        COALESCE(d.published_at, d.last_modified_header) AS published_at
                    FROM doc_fts
                    JOIN documents d ON doc_fts.rowid = d.id
                    LEFT JOIN urls u ON d.url_id = u.id
                    LEFT JOIN hosts h ON u.host_id = h.id
                    WHERE doc_fts MATCH ? AND {where_filter}
                    ORDER BY raw_bm25 ASC
                    LIMIT ?
                """  # noqa: S608
            else:
                tri_weights = f"{float(weights[0])}, {float(weights[1])}"
                sql = f"""
                    SELECT
                        d.id AS doc_id,
                        d.title,
                        COALESCE(d.canonical_url, d.final_url, u.url_key, '') AS url,
                        d.canonical_url,
                        d.canonical_group_id,
                        d.content_hash,
                        h.host,
                        bm25(doc_fts_tri, {tri_weights}) AS raw_bm25,
                        d.doc_type,
                        d.lang,
                        d.description,
                        d.word_count,
                        COALESCE(d.published_at, d.last_modified_header) AS published_at
                    FROM doc_fts_tri
                    JOIN documents d ON doc_fts_tri.rowid = d.id
                    LEFT JOIN urls u ON d.url_id = u.id
                    LEFT JOIN hosts h ON u.host_id = h.id
                    WHERE doc_fts_tri MATCH ? AND {where_filter}
                    ORDER BY raw_bm25 ASC
                    LIMIT ?
                """  # noqa: S608

            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
            candidates = [
                {
                    "doc_id": int(r["doc_id"]),
                    "title": str(r["title"] or ""),
                    "url": str(r["url"] or ""),
                    "canonical_url": str(r["canonical_url"]) if r["canonical_url"] else None,
                    "canonical_group_id": int(r["canonical_group_id"])
                    if r["canonical_group_id"] is not None
                    else None,
                    "content_hash": str(r["content_hash"]) if r["content_hash"] else None,
                    "host": str(r["host"] or ""),
                    "raw_bm25": float(r["raw_bm25"]),
                    "doc_type": str(r["doc_type"]),
                    "lang": str(r["lang"]),
                    "description": str(r["description"] or ""),
                    "word_count": int(r["word_count"] or 0),
                    "published_at": str(r["published_at"]) if r["published_at"] else None,
                }
                for r in rows
            ]
            return candidates, len(candidates)
        except sqlite3.OperationalError:
            return [], 0
