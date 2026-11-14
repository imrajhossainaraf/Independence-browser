# Release Notes: Independent Search Engine v0.1.0 (Release A)

**Release Date:** November 2026  
**Milestone:** M3 (Complete Release A Packaging)  
**Git Tag:** `v0.1.0`

---

## Highlights

Independent Search Engine v0.1.0 is a complete, self-hosted search engine and news platform engineered to run entirely on a personal Windows laptop. It features a read-only HTTP server, a responsive static web client, an offline-first search engine, an automated news aggregation system, and a polite, policy-enforced web crawler.

---

## Key Capabilities

### 1. Offline-First Ranked Search
- **Dual-Tokenizer Index**: Full-text search combining SQLite FTS5 (Porter stemmer with diacritic removal) and an FTS5 trigram index for CJK text and substring fallbacks.
- **Weighted Ranking Pipeline**: BM25 scoring with bounded freshness decay, phrase proximity bonuses, exact title matches, and host diversity demotion (at most 2 results per host in top 10).
- **Safe Snippets & Highlighting**: Deterministic excerpt extraction with HTML entity escaping and query term highlighting.
- **Did-You-Mean Suggestions**: Sub-millisecond spelling suggestions using `rapidfuzz` against `fts5vocab` with automated zero-result correction banners.

### 2. High-Confidence Search Panels
- **100% Verbatim Grounded Synopsis (Invariant 10)**: High-scoring queries display a synopsis panel composed strictly of verbatim substrings extracted from the top stored document, complete with source attribution and crawl timestamp.
- **Preview Cards**: Fallback cards providing domain, date, and lead text for top results.
- **Recent News Blocks**: Highlights up to 3 relevant news stories from the last 7 days.
- **Negative Query Discipline**: Strict suppression of synopsis panels on low-confidence or negative queries.

### 3. News Aggregation & Topic Clustering
- **Feed Ingestion**: RSS 2.0 and Atom 1.0 parsing with entity-bomb prevention via `defusedxml`.
- **Near-Duplicate Story Clustering**: Groups related coverage across multiple sources into cohesive top stories with pairwise precision >= 0.90.
- **14-Day Retention**: Automatic background retention manager that purges news items older than 14 days and enforces the 500-item database cap.

### 4. Polite & Safe Web Crawler
- **Single Choke Point (`SafeFetcher`)**: All outbound requests funnel through unforgeable single-use approval tokens issued by the host scheduler.
- **DNS Pinning & IP Validation**: Prevents DNS rebinding and rejects private, loopback, or link-local targets.
- **RFC 9309 Robots Conformance**: Full robots.txt compliance via Protego with 24-hour caching and crawl-delay honoring.
- **Subprocess PDF Extraction**: `pypdf` runs inside isolated subprocesses with 10-second timeouts and memory guards.
- **Air-Gapped Preflight Protection**: Real-mode crawling stays cryptographically locked until operator inputs are hashed into `data/preflight.approved.json`.

### 5. Robust Operations & Data Protection
- **Zero-Downtime Hot Backups**: Leverages SQLite Online Backup API accompanied by atomic snapshotting of hash-addressed raw storage.
- **Verified Restores**: Verifies backup manifest checksums and restores cleanly into empty target directories.
- **Operator Controls**: Dynamic blocking (host, domain, prefix), document removal, pausing (`data/PAUSE`), and emergency shutdown (`data/STOP`).

---

## Benchmark & Quality Results

All Section 7 quality gates, latency budgets, and memory targets have been verified on deterministic synthetic corpora (1,000 documents, 500 news items):

| Metric | Target / Gate | Verified Result | Status |
| :--- | :--- | :--- | :--- |
| **Search Latency (p50)** | < 80.0 ms | **12.87 ms** | **PASSED** |
| **Search Latency (p95)** | < 300.0 ms | **21.26 ms** | **PASSED** |
| **Home Latency (p95)** | < 200.0 ms | **2.16 ms** | **PASSED** |
| **Index Build Time** | < 120.0 s | **0.29 s** | **PASSED** |
| **Serving Memory (RSS)** | < 300.0 MB | **57.21 MB** | **PASSED** |
| **Crawl Memory (RSS)** | < 600.0 MB | **57.21 MB** | **PASSED** |
| **Precision@5 (Held-out)**| >= 0.60 | **0.6625** | **PASSED** |
| **MRR (Held-out)** | >= 0.70 | **0.9167** | **PASSED** |
| **nDCG@10 (Held-out)** | >= 0.70 | **0.8283** | **PASSED** |
| **Synopsis Grounding** | 100% Verbatim | **100% Verbatim** | **PASSED** |
| **Daily-Use Checklist** | 10/10 steps | **10/10 Passed** | **PASSED** |

---

## Clean-Checkout Verification

Release package verified via `scripts/release-check.ps1` including clean clone, dependency sync, format check, lint, type checking, test suite with coverage, fixture crawl, news poll, vertical slice demo, search smoke test, backup, and restore.
