"""Search evaluation engine computing P@5, P@10, MRR, nDCG@10, and latency reports (Step 12)."""

from __future__ import annotations

import csv
import hashlib
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from search_engine.evaluation.corpus import EVAL_CORPUS, EvalDocument
from search_engine.indexer import INDEX_VERSION, compress_text, rebuild_index
from search_engine.repositories import DocumentRepository, HostRepository, UrlRepository
from search_engine.search import SearchService

EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"


@dataclass(frozen=True, slots=True)
class QueryRecord:
    query_id: str
    query: str
    split: str  # "dev" or "held_out"
    category: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class QueryMetrics:
    query_id: str
    query: str
    split: str
    category: str
    p_at_5: float
    p_at_10: float
    mrr: float
    ndcg_10: float
    latency_ms: float
    result_count: int
    top_doc_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    timestamp: str
    split: str
    index_version: int
    ranking_version: str
    config_hash: str
    total_queries: int
    mean_p_at_5: float
    mean_p_at_10: float
    mean_mrr: float
    mean_ndcg_10: float
    latency_mean_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    zero_result_rate: float
    by_category: dict[str, dict[str, float]] = field(default_factory=dict)
    queries: list[QueryMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "split": self.split,
            "index_version": self.index_version,
            "ranking_version": self.ranking_version,
            "config_hash": self.config_hash,
            "total_queries": self.total_queries,
            "mean_p_at_5": round(self.mean_p_at_5, 4),
            "mean_p_at_10": round(self.mean_p_at_10, 4),
            "mean_mrr": round(self.mean_mrr, 4),
            "mean_ndcg_10": round(self.mean_ndcg_10, 4),
            "latency_mean_ms": round(self.latency_mean_ms, 2),
            "latency_p50_ms": round(self.latency_p50_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "zero_result_rate": round(self.zero_result_rate, 4),
            "by_category": self.by_category,
            "queries": [
                {
                    "query_id": q.query_id,
                    "query": q.query,
                    "split": q.split,
                    "category": q.category,
                    "p_at_5": round(q.p_at_5, 4),
                    "p_at_10": round(q.p_at_10, 4),
                    "mrr": round(q.mrr, 4),
                    "ndcg_10": round(q.ndcg_10, 4),
                    "latency_ms": round(q.latency_ms, 2),
                    "result_count": q.result_count,
                    "top_doc_ids": q.top_doc_ids,
                }
                for q in self.queries
            ],
        }

    def render_markdown(self) -> str:
        lines = [
            f"# Search Evaluation Report [{self.split.upper()} SET]",
            f"- **Date**: {self.timestamp}",
            f"- **Index Version**: {self.index_version}",
            f"- **Ranking Version**: {self.ranking_version}",
            f"- **Total Queries**: {self.total_queries}",
            "",
            "## Summary Metrics",
            "| Metric | Value |",
            "| :--- | :--- |",
            f"| **Precision@5** | {self.mean_p_at_5:.4f} |",
            f"| **Precision@10** | {self.mean_p_at_10:.4f} |",
            f"| **MRR** | {self.mean_mrr:.4f} |",
            f"| **nDCG@10** | {self.mean_ndcg_10:.4f} |",
            f"| **Zero-result rate** | {self.zero_result_rate:.2%} |",
            f"| **Latency p50** | {self.latency_p50_ms:.2f} ms |",
            f"| **Latency p95** | {self.latency_p95_ms:.2f} ms |",
            "",
            "## Category Breakdown",
            "| Category | Queries | P@5 | MRR | nDCG@10 |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for cat, stats in sorted(self.by_category.items()):
            cnt = int(stats["count"])
            p5 = stats["p_at_5"]
            mrr = stats["mrr"]
            ndcg = stats["ndcg_10"]
            lines.append(f"| {cat} | {cnt} | {p5:.3f} | {mrr:.3f} | {ndcg:.3f} |")
        return "\n".join(lines)


def calculate_dcg(grades: list[int], k: int = 10) -> float:
    """Compute Discounted Cumulative Gain at rank k."""
    dcg = 0.0
    for i, grade in enumerate(grades[:k]):
        gain = (2.0**grade) - 1.0
        discount = math.log2(i + 2.0)  # log2(rank + 1), 0-indexed i -> i + 2
        dcg += gain / discount
    return dcg


def calculate_ndcg(
    retrieved_doc_ids: list[int], ground_truth: dict[int, int], k: int = 10
) -> float:
    """Compute Normalized Discounted Cumulative Gain at rank k."""
    actual_grades = [ground_truth.get(doc_id, 0) for doc_id in retrieved_doc_ids]
    dcg = calculate_dcg(actual_grades, k)

    ideal_grades = sorted(ground_truth.values(), reverse=True)
    idcg = calculate_dcg(ideal_grades, k)

    if idcg <= 0.0:
        return 1.0 if dcg <= 0.0 else 0.0
    return dcg / idcg


def calculate_mrr(
    retrieved_doc_ids: list[int], ground_truth: dict[int, int], min_relevant: int = 2
) -> float:
    """Compute Reciprocal Rank: 1 / rank of first document with grade >= min_relevant."""
    for rank, doc_id in enumerate(retrieved_doc_ids, start=1):
        if ground_truth.get(doc_id, 0) >= min_relevant:
            return 1.0 / rank
    return 0.0


def calculate_precision_at_k(
    retrieved_doc_ids: list[int], ground_truth: dict[int, int], k: int = 5, min_relevant: int = 2
) -> float:
    """Compute Precision at rank k."""
    top_k = retrieved_doc_ids[:k]
    if not top_k:
        return 0.0
    relevant_count = sum(1 for doc_id in top_k if ground_truth.get(doc_id, 0) >= min_relevant)
    return relevant_count / len(top_k)


def load_queries(csv_path: Path = EVAL_DIR / "queries.csv") -> list[QueryRecord]:
    """Load query records from queries.csv."""
    records: list[QueryRecord] = []
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(
                QueryRecord(
                    query_id=row["query_id"].strip(),
                    query=row["query"].strip(),
                    split=row["split"].strip(),
                    category=row["category"].strip(),
                    notes=row.get("notes", "").strip(),
                )
            )
    return records


def load_judgments(csv_path: Path = EVAL_DIR / "judgments.csv") -> dict[str, dict[int, int]]:
    """Load judgments from judgments.csv into query_id -> {doc_id: grade}."""
    judgments: dict[str, dict[int, int]] = {}
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = row["query_id"].strip()
            doc_id = int(row["doc_id"].strip())
            grade = int(row["grade"].strip())
            if qid not in judgments:
                judgments[qid] = {}
            judgments[qid][doc_id] = grade
    return judgments


def populate_eval_database(
    conn: sqlite3.Connection, corpus: list[EvalDocument] = EVAL_CORPUS
) -> None:
    """Populate database with eval documents and build the FTS5 index."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    hosts = HostRepository(conn)
    urls = UrlRepository(conn)
    docs = DocumentRepository(conn)

    host_id = hosts.ensure("eval.local", "eval.local")

    for doc in corpus:
        url_id, _ = urls.add(doc.url, host_id, source="seed")
        docs.upsert_document(
            url_id=url_id,
            doc_type=doc.doc_type,
            final_url=doc.url,
            canonical_url=doc.url,
            title=doc.title,
            headings=doc.headings,
            description=doc.description,
            text_gz=compress_text(doc.body),
            lang=doc.lang,
            status="active",
            word_count=doc.word_count or len(doc.body.split()),
            published_at=doc.published_at,
        )

    rebuild_index(conn)


def evaluate_system(
    conn: sqlite3.Connection,
    *,
    split: str = "dev",  # "dev", "held_out", or "all"
    queries_file: Path = EVAL_DIR / "queries.csv",
    judgments_file: Path = EVAL_DIR / "judgments.csv",
    ranking_version: str = "bm25_v1",
) -> EvaluationReport:
    """Execute evaluation over queries matching the split."""
    all_queries = load_queries(queries_file)
    judgments = load_judgments(judgments_file)

    # Strictly enforce dev / held-out split
    if split == "dev":
        target_queries = [q for q in all_queries if q.split == "dev"]
    elif split == "held_out":
        target_queries = [q for q in all_queries if q.split == "held_out"]
    elif split == "all":
        target_queries = all_queries
    else:
        raise ValueError(f"Unknown split: {split!r}. Must be 'dev', 'held_out', or 'all'")

    service = SearchService(conn)
    query_metrics: list[QueryMetrics] = []
    latencies: list[float] = []

    for q in target_queries:
        t0 = time.perf_counter()
        response = service.search(q.query, limit=10)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies.append(elapsed_ms)

        retrieved_ids = [r.doc_id for r in response.results]
        q_judgments = judgments.get(q.query_id, {})

        p5 = calculate_precision_at_k(retrieved_ids, q_judgments, k=5)
        p10 = calculate_precision_at_k(retrieved_ids, q_judgments, k=10)
        mrr = calculate_mrr(retrieved_ids, q_judgments)
        ndcg = calculate_ndcg(retrieved_ids, q_judgments, k=10)

        query_metrics.append(
            QueryMetrics(
                query_id=q.query_id,
                query=q.query,
                split=q.split,
                category=q.category,
                p_at_5=p5,
                p_at_10=p10,
                mrr=mrr,
                ndcg_10=ndcg,
                latency_ms=elapsed_ms,
                result_count=len(retrieved_ids),
                top_doc_ids=retrieved_ids[:5],
            )
        )

    n = len(query_metrics)
    if n == 0:
        return EvaluationReport(
            timestamp=datetime.now(UTC).isoformat(),
            split=split,
            index_version=INDEX_VERSION,
            ranking_version=ranking_version,
            config_hash="",
            total_queries=0,
            mean_p_at_5=0.0,
            mean_p_at_10=0.0,
            mean_mrr=0.0,
            mean_ndcg_10=0.0,
            latency_mean_ms=0.0,
            latency_p50_ms=0.0,
            latency_p95_ms=0.0,
            zero_result_rate=0.0,
        )

    mean_p5 = sum(q.p_at_5 for q in query_metrics) / n
    mean_p10 = sum(q.p_at_10 for q in query_metrics) / n
    mean_mrr = sum(q.mrr for q in query_metrics) / n
    mean_ndcg = sum(q.ndcg_10 for q in query_metrics) / n
    zero_count = sum(1 for q in query_metrics if q.result_count == 0)
    zero_rate = zero_count / n

    latencies.sort()
    p50 = latencies[int(n * 0.50)]
    p95 = latencies[int(n * 0.95)] if n > 1 else latencies[0]
    mean_lat = sum(latencies) / n

    # Group by category
    categories: dict[str, list[QueryMetrics]] = {}
    for qm in query_metrics:
        categories.setdefault(qm.category, []).append(qm)

    by_category: dict[str, dict[str, float]] = {}
    for cat, items in categories.items():
        k = len(items)
        by_category[cat] = {
            "count": float(k),
            "p_at_5": sum(x.p_at_5 for x in items) / k,
            "mrr": sum(x.mrr for x in items) / k,
            "ndcg_10": sum(x.ndcg_10 for x in items) / k,
        }

    # Configuration hash
    config_summary = (
        f"index_v={INDEX_VERSION}:ranking={ranking_version}:corpus_size={len(EVAL_CORPUS)}"
    )
    config_hash = hashlib.sha256(config_summary.encode("utf-8")).hexdigest()[:16]

    return EvaluationReport(
        timestamp=datetime.now(UTC).isoformat(),
        split=split,
        index_version=INDEX_VERSION,
        ranking_version=ranking_version,
        config_hash=config_hash,
        total_queries=n,
        mean_p_at_5=mean_p5,
        mean_p_at_10=mean_p10,
        mean_mrr=mean_mrr,
        mean_ndcg_10=mean_ndcg,
        latency_mean_ms=mean_lat,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        zero_result_rate=zero_rate,
        by_category=by_category,
        queries=query_metrics,
    )
