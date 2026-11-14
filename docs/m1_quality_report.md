# Milestone M1 Quality Report: Core Search Engine

**Date**: 2026-11-14  
**Release**: v0.1.0 Milestone M1 (Steps 0 through 14 Complete)  
**Status**: Ready for Human Review  
**Repository**: [https://github.com/imrajhossainaraf/Independence-browser](https://github.com/imrajhossainaraf/Independence-browser)

---

## 1. Executive Summary

Milestone M1 marks the completion of the core search engine pipeline operating entirely offline on one laptop. The system features:
- Single choke-point network safety layer with pinned-IP TLS and unforgeable approvals (`SafeFetcher`)
- High-fidelity content extraction (HTML F1 = 0.993, plain text, isolated PDF worker, RSS/Atom feeds, sitemaps)
- Dual-tokenizer SQLite FTS5 inverted index (Porter unicode61 + CJK trigram) with atomic rebuild and consistency verification
- Explainable ranking with bounded freshness, source-quality adjustments, title/phrase boosts, and deterministic tie-breaking
- Canonical grouping by URL / content hash and host diversity cap (<= 2 results per host in top 10)
- Did-you-mean spelling suggestions and auto-correction banners via `doc_fts_vocab` and `rapidfuzz`
- Safe query-biased best-passage snippet extraction returning structured `HighlightSegment` tokens (zero HTML injection vectors)
- Complete offline test suite (461 tests, 88% overall test coverage, 0 network access)

---

## 2. Quality Metrics & Evaluation

Evaluation conducted on the 60-document designed fixture corpus (`src/search_engine/evaluation/corpus.py`) across 24 graded queries (16 dev, 8 held-out) with 78 relevance judgments (`eval/queries.csv`, `eval/judgments.csv`).

### Dev Query Set Metrics (Baseline vs Current)

| Metric | Step 12 Baseline | Milestone M1 (Step 14) | Status |
| :--- | :--- | :--- | :--- |
| **Precision@5** | 0.3125 | 0.3125 | Preserved (no regression) |
| **Precision@10** | 0.2476 | 0.2476 | Preserved (no regression) |
| **MRR (Mean Reciprocal Rank)** | 1.0000 | 1.0000 | Preserved (100% relevant top-1) |
| **nDCG@10** | 0.9046 | **0.9051** | **Improved (+0.0005)** |
| **Duplicate Results in Top 10** | 0 | **0** | **Target 0 Met** |
| **Max Results per Host (Top 10)** | 2 | **2** | **Target <= 2 Met** |
| **Snippet Term Coverage** | N/A | **100%** | **Target 100% Met** |
| **Zero-Result Rate** | 0.00% | 0.00% | Target 0.00% Met |
| **Latency p50 (Median)** | 0.25 ms | **0.57 ms** | Sub-millisecond |
| **Latency p95** | 0.66 ms | **3.90 ms** | Fast (< 5 ms) |

### Category Breakdown (Dev Set)

| Category | Queries | P@5 | MRR | nDCG@10 | Latency p50 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Exact** | 3 | 0.300 | 1.000 | 0.883 | 0.42 ms |
| **Informational** | 10 | 0.220 | 1.000 | 0.893 | 0.61 ms |
| **Multilingual** | 2 | 0.750 | 1.000 | 1.000 | 0.48 ms |
| **News** | 1 | 0.400 | 1.000 | 0.905 | 0.52 ms |

### Held-Out Evaluation (Single Measurement at Gate)

*Held-out queries are never tuned against.*

- **Total Queries**: 8
- **Precision@5**: 0.2729
- **Precision@10**: 0.2510
- **MRR**: 0.7500
- **nDCG@10**: 0.6847
- **Zero-result rate**: 25.00% (multilingual compound words)
- **Latency p50**: 0.99 ms

---

## 3. Security & Safety Invariants Verified

1. **XSS & Injection Immunity (Invariant I-16)**:
   - Snippets and title highlights are delivered strictly as structured `HighlightSegment` data (`text: str`, `is_match: bool`).
   - The backend produces zero HTML tags (`<b>`, `<mark>`, `<em>`), preventing HTML injection even when malicious source pages contain `<script>`, `onerror=`, or raw markup.
2. **Deterministic Tie-Breaking**:
   - Ties break deterministically on `(-score, canonical_url)`. Repeated runs on identical indexes produce identical result ordering.
3. **Pagination Integrity (Decision D7)**:
   - Slicing over canonical groups ensures consecutive pages never duplicate and never skip any result across page transitions.
4. **Host Diversity Constraint**:
   - At most 2 documents from any single registered domain are permitted in the top 10. Excess documents are demoted past the top 10 rather than discarded.
5. **Offline Test Suite (Invariant I-2)**:
   - 461 tests run with `--disable-socket` enforcing 0 public outbound network connections. Local loopback is permitted only for isolated fixture servers on `127.0.0.1`.

---

## 4. Verification Guide for the Human Operator

You can inspect and search the engine locally offline through PowerShell or the command line:

```powershell
# 1. Run the offline end-to-end demo
.\scripts\demo.ps1

# 2. Run sample CLI searches against the demo database
uv run search-engine search "spacecraft asteroid" --db data/demo.db
uv run search-engine search "python asynchronous event loop" --db data/demo.db
uv run search-engine search "scientific instruments telemetry" --db data/demo.db

# 3. View machine-readable JSON output with structured highlight segments
uv run search-engine search "spacecraft asteroid" --db data/demo.db --json

# 4. Run the quality evaluation harness
.\scripts\eval.ps1
```
