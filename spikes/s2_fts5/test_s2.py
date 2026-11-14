"""Unit tests for Spike S2: FTS5 Tokenizer & Weight Bake-off."""

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


def test_spike_s2_tokenizers_bakeoff() -> None:
    results = run_tokenizer_bakeoff()
    assert len(results) == 3

    tok_map = {r.tokenizer_name: r for r in results}

    # porter unicode61 remove_diacritics 2 achieves high MRR and nDCG
    porter = tok_map["porter unicode61 remove_diacritics 2"]
    assert porter.mrr >= 0.85
    assert porter.ndcg_10 >= 0.80

    # Trigram handles unspaced CJK query (Doc 30)
    trigram = tok_map["trigram"]
    assert trigram.cjk_success is True


def test_spike_s2_weight_presets() -> None:
    results = run_weight_presets_bakeoff()
    assert len(results) == 4

    preset_map = {r.preset_name: r for r in results}
    preset_b = preset_map["Preset B (D4 Default: 10:5:3:1:1)"]

    # Both presets achieve high ranking accuracy
    assert preset_b.mrr >= 0.95
    assert preset_b.ndcg_10 >= 0.90


def test_spike_s2_query_operators() -> None:
    conn = sqlite3.connect(":memory:")
    harness = FtsBakeoffHarness(conn)
    harness.setup_schema()
    harness.index_documents(DOCUMENTS)

    # 1. Exact phrase
    phrase_hits = harness.search_phrase("full text search")
    assert 1 in phrase_hits  # SQLite FTS5 guide
    assert 2 in phrase_hits or 5 in phrase_hits

    # 2. NEAR proximity
    near_hits = harness.search_near("sqlite", "fts5", max_distance=5)
    assert 1 in near_hits
    assert near_hits[0] == 1  # Top result is doc 1

    # 3. Prefix search
    prefix_hits = harness.search_prefix("microserv*")
    assert 21 in prefix_hits or 22 in prefix_hits  # Docker/Kubernetes microservices docs

    # 4. Diacritics matching (München vs munchen, café vs cafe)
    cafe_hits = harness.search_bm25("cafe restaurant", table="doc_fts")
    assert 26 in cafe_hits  # French cuisine doc with café

    munchen_hits = harness.search_bm25("munchen bahn", table="doc_fts")
    assert 27 in munchen_hits  # German travel doc with München

    conn.close()


def test_spike_s2_concurrent_wal_reads_and_writes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = str(Path(tmpdir) / "wal_concurrency_test.db")
        stats = run_concurrent_wal_test(db_file, num_writes=50, num_reads=100)

        assert stats.successful_reads == stats.total_reads
        assert stats.lock_errors == 0
        assert stats.read_p95_ms < 50.0  # Reads remain sub-50ms during writes
