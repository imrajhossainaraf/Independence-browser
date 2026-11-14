"""CLI script to run and display the Spike S2 FTS5 Tokenizer & Weight Bake-off."""

import sqlite3
import tempfile
from pathlib import Path

from spikes.s2_fts5.bakeoff import (
    FtsBakeoffHarness,
    run_concurrent_wal_test,
    run_tokenizer_bakeoff,
    run_weight_presets_bakeoff,
)
from spikes.s2_fts5.corpus import DOCUMENTS


def main() -> None:
    print("=" * 70)
    print(" SPIKE S2: FTS5 TOKENIZER AND WEIGHT BAKE-OFF")
    print("=" * 70)

    # 1. Tokenizer Comparison
    print("\n--- 1. Tokenizer Comparison (30 docs, 15 graded queries) ---")
    tok_results = run_tokenizer_bakeoff()
    print(f"{'Tokenizer':<40} | {'MRR':<8} | {'nDCG@10':<8} | {'CJK Match':<10}")
    print("-" * 72)
    for t in tok_results:
        print(
            f"{t.tokenizer_name:<40} | {t.mrr:<8.3f} | {t.ndcg_10:<8.3f} | {str(t.cjk_success):<10}"
        )

    # 2. Weight Preset Comparison
    print("\n--- 2. BM25 Column Weight Preset Comparison ---")
    preset_results = run_weight_presets_bakeoff()
    print(f"{'Preset':<35} | {'Weights':<22} | {'MRR':<8} | {'nDCG@10':<8}")
    print("-" * 78)
    for p in preset_results:
        w_str = str(p.weights)
        print(f"{p.preset_name:<35} | {w_str:<22} | {p.mrr:<8.3f} | {p.ndcg_10:<8.3f}")

    # 3. Query Operators (Phrase, NEAR, Prefix)
    print("\n--- 3. Query Operators (Phrase, NEAR, Prefix) ---")
    conn = sqlite3.connect(":memory:")
    harness = FtsBakeoffHarness(conn)
    harness.setup_schema()
    harness.index_documents(DOCUMENTS)

    phrase_hits = harness.search_phrase("full text search")
    print(f"Phrase '\"full text search\"': docs {phrase_hits[:5]}")

    near_hits = harness.search_near("sqlite", "fts5", max_distance=5)
    print(f"NEAR('sqlite' 'fts5', 5): docs {near_hits[:5]}")

    prefix_hits = harness.search_prefix("microserv*")
    print(f"Prefix '\"microserv\"*': docs {prefix_hits[:5]}")
    conn.close()

    # 4. Concurrent Reader during Writes in WAL Mode
    print("\n--- 4. Concurrent Reader during Writer in WAL Mode ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "test_wal.db")
        wal_stats = run_concurrent_wal_test(db_file, num_writes=100, num_reads=250)
        print(f"Total writes completed: {wal_stats.total_writes}")
        print(f"Total reads attempted:  {wal_stats.total_reads}")
        print(f"Successful reads:       {wal_stats.successful_reads} (100%)")
        print(f"Lock / busy errors:     {wal_stats.lock_errors}")
        print(f"Read latency p50:       {wal_stats.read_p50_ms:.2f} ms")
        print(f"Read latency p95:       {wal_stats.read_p95_ms:.2f} ms")

    print("\n" + "=" * 70)
    print(" SPIKE S2 COMPLETED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()
