"""Bake-off evaluation logic for Spike S2: FTS5 Tokenizers, BM25 Weights, Query Operators, and WAL Concurrency."""

import math
import sqlite3
import threading
import time
from dataclasses import dataclass

from spikes.s2_fts5.corpus import DOCUMENTS, GRADED_QUERIES, GradedQuery, MiniDoc


@dataclass
class EvalMetrics:
    mrr: float
    ndcg_10: float
    p_at_5: float
    total_queries: int


@dataclass
class TokenizerComparison:
    tokenizer_name: str
    mrr: float
    ndcg_10: float
    cjk_success: bool


@dataclass
class WeightPresetComparison:
    preset_name: str
    weights: tuple[float, float, float, float, float]
    mrr: float
    ndcg_10: float


@dataclass
class ConcurrentWalStats:
    total_reads: int
    successful_reads: int
    lock_errors: int
    read_p50_ms: float
    read_p95_ms: float
    total_writes: int


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
    """Compute Normalized DCG at rank k."""
    actual_grades = [ground_truth.get(doc_id, 0) for doc_id in retrieved_doc_ids]
    dcg = calculate_dcg(actual_grades, k)

    # Ideal ranking: sorted descending
    ideal_grades = sorted(ground_truth.values(), reverse=True)
    idcg = calculate_dcg(ideal_grades, k)

    if idcg <= 0.0:
        return 1.0 if dcg <= 0.0 else 0.0
    return dcg / idcg


def calculate_mrr(
    retrieved_doc_ids: list[int], ground_truth: dict[int, int], min_relevant_grade: int = 2
) -> float:
    """Compute Reciprocal Rank: 1 / rank of first document with grade >= min_relevant_grade."""
    for rank, doc_id in enumerate(retrieved_doc_ids, start=1):
        if ground_truth.get(doc_id, 0) >= min_relevant_grade:
            return 1.0 / rank
    return 0.0


def calculate_precision_at_k(
    retrieved_doc_ids: list[int],
    ground_truth: dict[int, int],
    k: int = 5,
    min_relevant_grade: int = 2,
) -> float:
    """Compute Precision at k."""
    top_k = retrieved_doc_ids[:k]
    if not top_k:
        return 0.0
    relevant_count = sum(1 for doc_id in top_k if ground_truth.get(doc_id, 0) >= min_relevant_grade)
    return relevant_count / len(top_k)


class FtsBakeoffHarness:
    """Harness to create, populate, and query FTS5 indexes in memory or file."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.row_factory = sqlite3.Row

    def setup_schema(self, tokenizer: str = "porter unicode61 remove_diacritics 2") -> None:
        """Create main document table and FTS5 virtual tables."""
        cur = self.db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                headings TEXT NOT NULL,
                description TEXT NOT NULL,
                body TEXT NOT NULL,
                url_tokens TEXT NOT NULL,
                lang TEXT NOT NULL DEFAULT 'en'
            )
            """
        )

        cur.execute("DROP TABLE IF EXISTS doc_fts")
        cur.execute(
            f"""
            CREATE VIRTUAL TABLE doc_fts USING fts5(
                title,
                headings,
                description,
                body,
                url_tokens,
                content='documents',
                content_rowid='id',
                tokenize = '{tokenizer}'
            )
            """
        )

        cur.execute("DROP TABLE IF EXISTS doc_fts_tri")
        cur.execute(
            """
            CREATE VIRTUAL TABLE doc_fts_tri USING fts5(
                title,
                body_prefix,
                content='documents',
                content_rowid='id',
                tokenize = 'trigram'
            )
            """
        )

        cur.execute("DROP TABLE IF EXISTS doc_fts_vocab")
        cur.execute(
            """
            CREATE VIRTUAL TABLE doc_fts_vocab USING fts5vocab(
                doc_fts,
                'row'
            )
            """
        )
        self.db.commit()

    def index_documents(self, docs: list[MiniDoc]) -> None:
        """Populate documents table and FTS index."""
        cur = self.db.cursor()
        for doc in docs:
            cur.execute(
                """
                INSERT OR REPLACE INTO documents (id, title, headings, description, body, url_tokens, lang)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.id,
                    doc.title,
                    doc.headings,
                    doc.description,
                    doc.body,
                    doc.url_tokens,
                    doc.lang,
                ),
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO doc_fts (rowid, title, headings, description, body, url_tokens)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc.id, doc.title, doc.headings, doc.description, doc.body, doc.url_tokens),
            )
            # Trigram index indexes title and body_prefix (up to 5000 chars per D4)
            cur.execute(
                """
                INSERT OR REPLACE INTO doc_fts_tri (rowid, title, body_prefix)
                VALUES (?, ?, ?)
                """,
                (doc.id, doc.title, doc.body[:5000]),
            )
        self.db.commit()

    def search_bm25(
        self,
        query: str,
        weights: tuple[float, float, float, float, float] = (10.0, 5.0, 3.0, 1.0, 1.0),
        table: str = "doc_fts",
        limit: int = 10,
    ) -> list[int]:
        """Search using weighted bm25() score."""
        cur = self.db.cursor()
        w_str = ", ".join(str(w) for w in weights)

        # For CJK queries in trigram table, remove spaces between CJK chars
        is_cjk = any("\u4e00" <= char <= "\u9fff" for char in query)
        if is_cjk and table == "doc_fts_tri":
            query = "".join(query.split())

        # Tokenize query into AND terms
        terms = [t for t in query.strip().split() if t]
        if not terms:
            return []

        # Safe quoting for FTS5 terms
        quoted_terms = ['"' + t.replace('"', '""') + '"' for t in terms]
        and_expr = " AND ".join(quoted_terms)

        try:
            if table == "doc_fts":
                sql = f"""
                    SELECT rowid, bm25(doc_fts, {w_str}) AS score
                    FROM doc_fts
                    WHERE doc_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                """  # noqa: S608
                cur.execute(sql, (and_expr, limit))
                rows = cur.fetchall()
            else:
                # Trigram table has 2 columns: title, body_prefix
                tri_weights = f"{weights[0]}, {weights[3]}"
                sql = f"""
                    SELECT rowid, bm25(doc_fts_tri, {tri_weights}) AS score
                    FROM doc_fts_tri
                    WHERE doc_fts_tri MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                """  # noqa: S608
                cur.execute(sql, (and_expr, limit))
                rows = cur.fetchall()

            and_results = [row["rowid"] for row in rows]

            # If AND returns fewer than 3 results, supplement with OR fallback (preserving AND matches at the top)
            if len(and_results) < 3 and len(quoted_terms) > 1:
                or_expr = " OR ".join(quoted_terms)
                if table == "doc_fts":
                    sql = f"""
                        SELECT rowid, bm25(doc_fts, {w_str}) AS score
                        FROM doc_fts
                        WHERE doc_fts MATCH ?
                        ORDER BY score ASC
                        LIMIT ?
                    """  # noqa: S608
                    cur.execute(sql, (or_expr, limit))
                    or_rows = cur.fetchall()
                else:
                    tri_weights = f"{weights[0]}, {weights[3]}"
                    sql = f"""
                        SELECT rowid, bm25(doc_fts_tri, {tri_weights}) AS score
                        FROM doc_fts_tri
                        WHERE doc_fts_tri MATCH ?
                        ORDER BY score ASC
                        LIMIT ?
                    """  # noqa: S608
                    cur.execute(sql, (or_expr, limit))
                    or_rows = cur.fetchall()

                for row in or_rows:
                    if row["rowid"] not in and_results:
                        and_results.append(row["rowid"])

            return and_results[:limit]
        except sqlite3.OperationalError:
            return []

    def search_phrase(self, phrase: str, limit: int = 10) -> list[int]:
        """Execute exact phrase query."""
        cur = self.db.cursor()
        clean = phrase.replace('"', '""')
        expr = f'"{clean}"'
        cur.execute(
            """
            SELECT rowid, bm25(doc_fts) AS score
            FROM doc_fts
            WHERE doc_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
            """,
            (expr, limit),
        )
        return [row["rowid"] for row in cur.fetchall()]

    def search_near(
        self, term_a: str, term_b: str, max_distance: int = 5, limit: int = 10
    ) -> list[int]:
        """Execute NEAR proximity query."""
        cur = self.db.cursor()
        a = term_a.replace('"', '""')
        b = term_b.replace('"', '""')
        expr = f'NEAR("{a}" "{b}", {max_distance})'
        cur.execute(
            """
            SELECT rowid, bm25(doc_fts) AS score
            FROM doc_fts
            WHERE doc_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
            """,
            (expr, limit),
        )
        return [row["rowid"] for row in cur.fetchall()]

    def search_prefix(self, prefix: str, limit: int = 10) -> list[int]:
        """Execute prefix wildcard query."""
        cur = self.db.cursor()
        clean = prefix.replace('"', "").rstrip("*")
        expr = f'"{clean}"*'
        cur.execute(
            """
            SELECT rowid, bm25(doc_fts) AS score
            FROM doc_fts
            WHERE doc_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
            """,
            (expr, limit),
        )
        return [row["rowid"] for row in cur.fetchall()]


def evaluate_query_set(
    harness: FtsBakeoffHarness,
    queries: list[GradedQuery],
    weights: tuple[float, float, float, float, float] = (10.0, 5.0, 3.0, 1.0, 1.0),
    use_trigram_for_cjk: bool = True,
) -> EvalMetrics:
    """Evaluate a query set on the given harness and compute aggregate metrics."""
    mrr_list = []
    ndcg_list = []
    p5_list = []

    for q in queries:
        # Check if CJK query (e.g. Chinese characters)
        is_cjk = any("\u4e00" <= char <= "\u9fff" for char in q.query)
        table = "doc_fts_tri" if (is_cjk and use_trigram_for_cjk) else "doc_fts"

        results = harness.search_bm25(q.query, weights=weights, table=table, limit=10)
        mrr = calculate_mrr(results, q.grades)
        ndcg = calculate_ndcg(results, q.grades, k=10)
        p5 = calculate_precision_at_k(results, q.grades, k=5)

        mrr_list.append(mrr)
        ndcg_list.append(ndcg)
        p5_list.append(p5)

    n = len(queries)
    return EvalMetrics(
        mrr=sum(mrr_list) / n if n else 0.0,
        ndcg_10=sum(ndcg_list) / n if n else 0.0,
        p_at_5=sum(p5_list) / n if n else 0.0,
        total_queries=n,
    )


def run_tokenizer_bakeoff() -> list[TokenizerComparison]:
    """Compare tokenizers: unicode61, porter unicode61 remove_diacritics 2, and trigram."""
    tokenizers = [
        ("unicode61", "unicode61"),
        ("porter unicode61 remove_diacritics 2", "porter unicode61 remove_diacritics 2"),
        ("trigram", "trigram"),
    ]

    comparisons: list[TokenizerComparison] = []

    for label, tok_def in tokenizers:
        conn = sqlite3.connect(":memory:")
        harness = FtsBakeoffHarness(conn)
        harness.setup_schema(tokenizer=tok_def)
        harness.index_documents(DOCUMENTS)

        # If evaluating trigram directly, use doc_fts_tri or main table with trigram
        metrics = evaluate_query_set(
            harness, GRADED_QUERIES, use_trigram_for_cjk=(label != "unicode61")
        )

        # Test CJK query specifically
        cjk_query = "北京 气候 峰会"
        table = "doc_fts_tri" if "trigram" in label else "doc_fts"
        cjk_results = harness.search_bm25(cjk_query, table=table)
        cjk_hit = 30 in cjk_results  # Doc 30 is the Beijing climate summit doc

        comparisons.append(
            TokenizerComparison(
                tokenizer_name=label,
                mrr=metrics.mrr,
                ndcg_10=metrics.ndcg_10,
                cjk_success=cjk_hit,
            )
        )
        conn.close()

    return comparisons


def run_weight_presets_bakeoff() -> list[WeightPresetComparison]:
    """Compare BM25 weight presets on doc_fts(title, headings, description, body, url_tokens)."""
    presets = [
        ("Preset A (Flat 1:1:1:1:1)", (1.0, 1.0, 1.0, 1.0, 1.0)),
        ("Preset B (D4 Default: 10:5:3:1:1)", (10.0, 5.0, 3.0, 1.0, 1.0)),
        ("Preset C (Moderate: 5:2.5:2:1:0.5)", (5.0, 2.5, 2.0, 1.0, 0.5)),
        ("Preset D (Heavy Title: 20:5:2:1:1)", (20.0, 5.0, 2.0, 1.0, 1.0)),
    ]

    results: list[WeightPresetComparison] = []
    conn = sqlite3.connect(":memory:")
    harness = FtsBakeoffHarness(conn)
    harness.setup_schema("porter unicode61 remove_diacritics 2")
    harness.index_documents(DOCUMENTS)

    for name, w in presets:
        metrics = evaluate_query_set(harness, GRADED_QUERIES, weights=w)
        results.append(
            WeightPresetComparison(
                preset_name=name,
                weights=w,
                mrr=metrics.mrr,
                ndcg_10=metrics.ndcg_10,
            )
        )

    conn.close()
    return results


def run_concurrent_wal_test(
    db_path: str, num_writes: int = 100, num_reads: int = 200
) -> ConcurrentWalStats:
    """Simulate a background writer inserting/updating documents while a foreground reader searches."""
    # Initialize DB with WAL mode
    init_conn = sqlite3.connect(db_path)
    init_conn.execute("PRAGMA journal_mode = WAL")
    init_conn.execute("PRAGMA synchronous = NORMAL")
    init_conn.execute("PRAGMA busy_timeout = 5000")
    harness = FtsBakeoffHarness(init_conn)
    harness.setup_schema()
    harness.index_documents(DOCUMENTS)
    init_conn.close()

    read_latencies_ms: list[float] = []
    lock_errors = 0
    successful_reads = 0
    writer_done = threading.Event()

    def writer_worker() -> None:
        try:
            w_conn = sqlite3.connect(db_path, timeout=5.0)
            w_conn.execute("PRAGMA busy_timeout = 5000")
            for i in range(num_writes):
                # Update an existing document or insert a dummy doc
                doc_id = 100 + i
                title = f"Dynamic Document Update {i}"
                body = f"Continuous updates to SQLite WAL mode document index iteration {i} testing concurrency."
                w_conn.execute(
                    """
                    INSERT OR REPLACE INTO documents (id, title, headings, description, body, url_tokens)
                    VALUES (?, ?, 'Headings', 'Desc', ?, 'url')
                    """,
                    (doc_id, title, body),
                )
                w_conn.execute(
                    """
                    INSERT OR REPLACE INTO doc_fts (rowid, title, headings, description, body, url_tokens)
                    VALUES (?, ?, 'Headings', 'Desc', ?, 'url')
                    """,
                    (doc_id, title, body),
                )
                w_conn.commit()
                time.sleep(0.002)  # Short pause between writes
            w_conn.close()
        finally:
            writer_done.set()

    def reader_worker() -> None:
        nonlocal lock_errors, successful_reads
        r_conn = sqlite3.connect(db_path, timeout=5.0)
        r_conn.row_factory = sqlite3.Row
        r_conn.execute("PRAGMA busy_timeout = 5000")

        queries = [
            "sqlite",
            "python",
            "concurrency",
            "quantum",
            "climate",
            "neural",
            "docker",
            "continuous updates",
        ]

        count = 0
        while count < num_reads or not writer_done.is_set():
            q = queries[count % len(queries)]
            t0 = time.perf_counter()
            try:
                cur = r_conn.cursor()
                cur.execute(
                    """
                    SELECT rowid, bm25(doc_fts) AS score
                    FROM doc_fts
                    WHERE doc_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT 5
                    """,
                    (f'"{q}"',),
                )
                _ = cur.fetchall()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                read_latencies_ms.append(elapsed_ms)
                successful_reads += 1
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    lock_errors += 1
                else:
                    raise
            count += 1
            time.sleep(0.001)

        r_conn.close()

    writer_t = threading.Thread(target=writer_worker)
    reader_t = threading.Thread(target=reader_worker)

    writer_t.start()
    reader_t.start()

    writer_t.join()
    reader_t.join()

    read_latencies_ms.sort()
    n_reads = len(read_latencies_ms)
    p50 = read_latencies_ms[int(n_reads * 0.50)] if n_reads else 0.0
    p95 = read_latencies_ms[int(n_reads * 0.95)] if n_reads else 0.0

    return ConcurrentWalStats(
        total_reads=n_reads,
        successful_reads=successful_reads,
        lock_errors=lock_errors,
        read_p50_ms=p50,
        read_p95_ms=p95,
        total_writes=num_writes,
    )
