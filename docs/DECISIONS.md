# Decisions (ADR log)

Template for each entry: **ADR-NNN, date, status.** Context. Decision. Alternatives. Consequences.

## ADR-001, 2026-11-04, accepted: Python 3.12 via uv
Context: the machine has Python 3.14.7; wheels for planned libraries may lag. Decision: pin Python 3.12 with `uv` (human approved). Alternatives: use 3.14. Consequences: `.python-version` and the Docker image use 3.12.

## ADR-002, 2026-11-04, accepted: Docker added, GitHub Actions deferred
Context: the human asked for Docker and CI/CD; BUILD_PLAN lists CI as optional and has no Docker. Decision: add a Dockerfile (`check` and `runtime` stages) in Step 1; skip GitHub Actions for now (human chose Docker only). Alternatives: Actions on windows-latest and ubuntu-latest. Consequences: the containerized check is the repeatable gate; Actions can be added later without code changes.

## ADR-003, 2026-11-04, accepted: build backend and dev tooling
Context: a src-layout package needs a build backend. Decision: `hatchling` (MIT) as backend; dev tools as listed in BUILD_PLAN Section 4 in a `dev` dependency group; no runtime dependencies yet. Consequences: each runtime dependency gets its own ADR when introduced.

## ADR-004, 2026-11-04, accepted: one check runner
Context: scripts and Makefile must not drift. Decision: `scripts/run_check.py` holds the stage list; `check.ps1`, `make check`, and the Docker `check` stage all call it. Consequences: changing the check means editing one file.

## ADR-005, 2026-11-04, accepted: waived per-step approval stops
Context: the human said to deliver the full finished project and approved all permissions. Decision: continue step by step with a commit and tag per step but without stopping for `approved: step N`. Mandatory stops from BUILD_INSTRUCTIONS Section 2 rule 5 still apply (real internet, source choice, legal, off-list dependencies, thresholds). Consequences: work halts at Step 29 until the human provides the Phase 7 prerequisites.

## ADR-006, 2026-11-04, accepted: Spike S1 result, pinned-IP TLS is GO
Context: rule S3 needs connections pinned to a validated IP while the certificate is verified against the original hostname. Decision: GO. Send the request to `https://<ip>:<port>/` with an explicit `Host` header and the httpx `sni_hostname` request extension (httpx 0.28.1, httpcore 1.0.9); no custom network backend needed. Evidence (spikes/s1_pinned_transport, 8 tests, local trustme certificates, no public internet): Host header correct; certificate checked against the hostname (a certificate for another name and an untrusted CA both fail with ConnectError); redirects followed manually with every hop re-resolved and re-pinned; IPv6 (::1) works; read timeouts raise; HAZARD found: with keep-alive the pool key is the IP, so a second hostname on the same IP and port reuses the first connection and skips its certificate check. Fix (mandatory in Step 5): disable keep-alive (`max_keepalive_connections=0`) or use a fresh client per request, and keep a test for it. Fallback if this ever regresses: a custom httpcore network backend that wraps the socket with `server_hostname` set. Consequences: Step 5 implements `Transport` this way; pool reuse across hosts is a tested prohibition.

## ADR-007, 2026-11-04, accepted: dependencies httpx and trustme
httpx (BSD-3-Clause) is the only HTTP client and is imported only from `search_engine/net`; trustme (MIT or Apache-2.0) is dev-only for local TLS fixtures. Both are on the BUILD_PLAN Section 4 list.

## ADR-008, 2026-11-04, accepted: configuration limits beyond the plan
Context: Step 2 needs values for total storage, runtime, timeouts, backoff, and synopsis thresholds that BUILD_PLAN does not state. Decision: defaults of 1 GiB storage (ceiling 2 GiB), 3600 s runtime (ceiling 6 h), connect 10 s, read 20 s, DNS 5 s, retries 3, backoff 2 s to 60 s, synopsis 40 to 300 characters with placeholder coverage 0.6, all adjustable downward and tuned only on dev queries (Step 27). A draft rule 'max_pages_per_host must not exceed max_pages' was dropped (not in the plan, and it made lowering max_pages awkward); its test was removed with it. Feature flags for Release B and news_images_enabled are rejected until their steps exist. Consequences: the human may revise these numbers with a recorded reason.

## ADR-009, 2026-11-04, accepted: URL handling choices (Step 3)
Context: D1/D2 leave details open. Decisions: (1) tldextract 5.4 (BSD-3-Clause) runs offline from its bundled snapshot (no suffix-list URLs, no cache dir); it depends on `requests`, which our code never imports and tldextract never calls with these settings, tests block sockets; the architecture test covers only our own source. (2) Hosts without a registered domain (single labels, unknown suffixes such as .test or .example) are rejected, so fixture hostnames in tests use real suffixes like example.com with a fake resolver. (3) All IPv4-mapped, NAT64, 6to4 and Teredo IPv6 addresses count as non-public. (4) Public IP literals are allowed; their registered domain is the address itself. (5) Spaces inside a URL are percent-encoded; other control characters and backslashes are rejected. (6) Ports other than 80 and 443 are rejected except under the test-only loopback exception, which allows only loopback hosts and never relaxes other checks. (7) news_sources.txt format is `feed URL | name | category | poll minutes | images yes/no` with the last two optional; intervals under 15 minutes are reported as problems. Consequences: later steps normalize every URL through crawler.normalize.

## ADR-010: Storage layer (Step 4)

Plain sqlite3 with one writer connection (BEGIN IMMEDIATE) and per-call read-only readers (WAL). Forward-only numbered .sql migrations, each in its own transaction, with an online backup before upgrading. Raw pages are stored as gzip at data/raw/ab/cd/<sha256>.gz and images at data/images/ab/cd/<sha256>-<size>.webp; paths come only from hashes. Writes go through a temp file, fsync and os.replace, retrying on Windows PermissionError. recover() reports orphan files and rows with missing files, and removes only interrupted temp files. Migrations ship inside the package, so they are included in the wheel and the Docker image.

## ADR-011: Network safety layer and fixture server (Step 5)

Outbound HTTP connections are strictly confined to `src/search_engine/net/` through a project-owned `Transport` protocol (`TransportRequest`, `TransportResponse`, `Transport`, `FakeTransport`).
The production implementation `HttpxTransport` pins requests to pre-validated IP addresses using the URL format `https://<pinned_ip>:<port>/path` while setting the `Host` header and `sni_hostname` TLS extension to the original domain name.
Connection reuse across hosts (the hazard discovered in ADR-006) is eliminated by configuring `max_keepalive_connections=0`, forcing a fresh TLS handshake per request.
A safety gate inside `HttpxTransport.send` verifies that the target IP satisfies the IP policy immediately before opening any socket.
`SystemResolver` returns all A and AAAA records with strict timeouts; `FakeResolver` supports deterministic testing and sequential DNS rebinding simulations.
Hosts resolving to any non-public or mapped addresses are rejected.
A scriptable HTTP/HTTPS `FixtureServer` on `127.0.0.1` provides coverage for normal pages, robots variants, redirect chains and loops, slow responses, huge payloads, gzip bombs, wrong content types, 429/500 errors, dropped sockets, RSS feeds with media enclosures, and valid/malformed images.

## ADR-012: Safe HTTP Fetcher (Step 6)

`SafeFetcher` (`src/search_engine/crawler/fetcher.py`) acts as the single network choke point (invariant I-1) for crawler traffic, delegating to the project-owned `Transport` interface without directly importing `httpx`.
Fetching is protected by unforgeable, single-use approval capabilities (`FetchApproval` and `RobotsFetchApproval`) created only through sentinel-guarded scheduler factories; attempts to reuse, forge, or cross-call approvals fail immediately.
Automatic redirects are disabled; redirects are returned as `Redirect` records for re-submission through the scheduler and approval pipeline, with a bounded `follow` helper capping hops at 5.
Responses are streamed under strict byte limits (D9) and timeouts, preventing gzip bombs and oversized payloads.
Payloads are checked against Content-Type allowlists and scanned via magic-byte sniffing (`sniff_content`) to strictly reject executables (DOS/PE, ELF, Mach-O, Java), SVG images (invariant I-5), animated GIFs, archives, and mislabeled binary formats.
Status codes are classified into `success`, `retryable` (408, 425, 429, 5xx, network drops, timeouts), and `permanent` (4xx). Rate limits (429) extract `Retry-After` capped at `max_crawl_delay_seconds`.
No cookies are sent or stored (invariant I-7).

## ADR-013: Robots policy and host scheduler (Step 7)

`Protego` (BSD-3-Clause, pure Python) was added as the RFC 9309 robots parser behind the project-owned `RobotsPolicy` interface.
`RobotsCache` caches policies per origin (`<scheme>://<host>:<port>`) in memory and SQLite with D3 TTLs: 24 hours for parsed (2xx) and allow_all (4xx), and 10 minutes for disallow_all (5xx, timeouts, refused redirect hops).
`Scheduler.approve(url)` enforces strict gate ordering before issuing a `FetchApproval`: (1) normalization, (2) blocklist/allowlist, (3) DNS and public IP policy, (4) robots rules and Crawl-delay cap, (5) per-host politeness next-allowed time, and (6) per-host concurrency (1) and global concurrency caps.
`Scheduler.approve_robots(origin)` issues a single-use `RobotsFetchApproval` bound to the host's own `/robots.txt`, skipping only the robots rule check. Up to 5 redirect hops are supported, provided each target stays on an allowlisted host.
Politeness delays and exponential backoff with jitter on retryable failures are tracked and updated via `Scheduler.release()`.

## ADR-014: Spike S5 result, text extraction bake-off is GO (Spike S5)

Context: Step 8 requires deterministic, searchable text extraction from HTML without boilerplate leakage (nav, headers, footers, cookie banners, advertisements). We need to determine the primary and fallback extraction strategy across diverse web pages (articles, documentation, blogs, forums, product pages, wikis).
Decision: GO. Adopt a combined extraction pipeline with `trafilatura` (2.3.1, Apache-2.0) as the primary extractor and `selectolax` (Lexbor backend, MIT) as fallback when the extracted token count is below 40 tokens and the fallback yields more content.
Evaluation: Evaluated across 20 diverse HTML fixtures in `spikes/s5_extraction/fixtures/` with hand-crafted gold text:
- Combined average token F1: 0.993 (requirement >= 0.90)
- Combined minimum token F1: 0.953 (requirement >= 0.80)
- Boilerplate leakage rate: 0.0% (no cookie notices, nav links, footers, or ads leaked into gold output)
Dependencies: `trafilatura` (Apache-2.0), `selectolax` (MIT), `charset-normalizer` (MIT), and `python-dateutil` (BSD-3-Clause) added per Section 4 list.
Consequences: Step 8 will use this hybrid strategy in `src/search_engine/extract/html.py`.

## ADR-015: HTML parsing and document extraction (Step 8)

Context: Step 8 requires deterministic, searchable document extraction from HTML into a structured `ExtractedDocument` without network dependencies, crashes, or boilerplate leakage.
Decision:
- Created `src/search_engine/extract/models.py` defining `ExtractedDocument`, `OutgoingLink`, `RobotsMeta`, and `MediaRef`.
- Implemented `src/search_engine/extract/html.py` with `extract_html`:
  - Primary text extraction via `trafilatura` (2.3.1) with `selectolax` (Lexbor backend) fallback when token count is low (<40 tokens) per ADR-014.
  - Extracts: final URL, separate normalized canonical URL, title (with og:title, twitter:title, and h1 fallbacks), meta description, site name, headings (h1..h6 in document order), language hint per D10 (html lang attribute, Content-Language header/meta, Unicode-script heuristic for Cyrillic/CJK/Kana/Hangul/Arabic/Hebrew/Devanagari/Greek/Thai, else 'und'), published date (ISO 8601 parsed from meta tags, JSON-LD scripts, or `<time>` elements), outgoing normalized links with `nofollow` flag, robots meta (`noindex`, `nofollow`, `noarchive`, `nosnippet`, `none`, `all`), SHA-256 content hash, media references (image alt text, figure captions, video and audio sources, transcript links), image candidate URLs (`og:image`, `twitter:image`, and first content image >= 200x200), word count, and structured warnings.
  - Memory and DOS bounds: input capped at `max_bytes` (default 2 MiB) with truncation warnings; charset decoding falls back gracefully through UTF-8, meta charset, `charset-normalizer`, and replacement; hostile nesting (1500+ levels) and giant attributes parse safely without recursion or hang; unexpected parse errors fail closed to empty documents or warnings.
  - Verification: 20 checked-in diverse fixtures in `tests/fixtures/html/` with hand-crafted gold text passed all quality thresholds (avg F1 = 0.993, min F1 = 0.953, 0.0% boilerplate leakage).
Consequences: Content adapters (Step 9) and the FTS5 indexer (Step 10) can consume `ExtractedDocument` directly.

## ADR-016: Spike S3 result, subprocess isolation is GO (Spike S3)

Context: D10 and D17 require PDF extraction (Step 9) and image decoding (Step 19) to run in an isolated worker subprocess with spawn semantics, a hard timeout, process-tree termination, and a memory limit (default 512 MiB). On Windows, we prototyped both Win32 Job Objects (via `ctypes`) and `psutil` tree monitoring.
Findings & Evaluation (`spikes/s3_isolation`):
- Startup overhead: Bare Popen ~590ms, Job Object ~397ms, psutil monitor ~459ms (negligible delta relative to Python VM startup).
- Memory limit enforcement: Job Object sets `JOB_OBJECT_LIMIT_JOB_MEMORY`, causing the Windows kernel to immediately refuse allocations above the limit (producing instant `MemoryError` in child). `psutil` monitoring checks RSS across the entire process tree and terminates it on breach.
- Timeout & tree termination: `kill_process_tree` terminates the parent and recursively all descendant processes on timeout or memory breach, leaving zero orphan processes.
Decision: GO. Adopt a dual-layer isolation approach:
1. On Windows, worker subprocesses are bound to a Win32 Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and kernel memory limits.
2. Cross-platform timeout and process tree killing are enforced via `psutil` (7.2.2, BSD-3-Clause).
Consequences: Step 9 will build `src/search_engine/extract/pdf.py` using this isolated worker harness.

## ADR-017: Content adapters (Step 9)

Context: Step 9 requires multi-format content handling (plain text, PDF via isolated worker, RSS/Atom feeds, XML sitemaps) feeding the indexer and crawl frontier without crashing, memory exhaustion, or security bypass.
Decision:
- Created content adapters in `src/search_engine/extract/`:
  - `PlainTextAdapter` (`text.py`): Extracts UTF-8/charset-normalized text, titles from first line or filename, word counts, and language hints.
  - `PdfAdapter` (`pdf.py`, `pdf_worker.py`): Isolated worker subprocess running `pypdf` (7.1.0, BSD-3-Clause) with Win32 Job Object and `psutil` tree monitoring per ADR-016. Enforces hard timeouts (15s), memory limits (512 MiB), and page limits (100 pages). Extracts text, metadata (title, author), and bookmarks. Safely handles malformed/corrupted PDFs with `AdapterStatus.ERROR` or `DEGRADED`.
  - `FeedAdapter` (`feed.py`): RSS 2.0 and Atom 1.0 feed parser using `defusedxml.ElementTree` (blocking entity expansion and external DTDs). Emits `NewsItemCandidate` items with title, link, summary, published_date, author, and categories for news ingestion (Step 18).
  - `SitemapAdapter` (`sitemap.py`): XML sitemap and sitemap index parser using `defusedxml.ElementTree`. Supports standard XML and gzip-compressed sitemaps (`.xml.gz` and magic bytes `1f 8b`) with bounded decompression (capped at 10 MiB) to prevent gzip bombs. Yields `SitemapUrl` entries with normalized URLs, lastmod, changefreq, and priority.
  - `AdapterRegistry` and `extract_content` (`adapters.py`): Content-Type and URL path dispatcher routing to appropriate adapters, falling back gracefully to text or empty documents on unhandled types.
- Verification: 17 unit tests in `tests/unit/test_adapters.py` covering all adapters, isolated worker memory and timeout traps, defusedxml security, gzip bombs, and corrupt inputs. 400 project tests passed with 89% total coverage.
Consequences: Crawler, indexer, and news pipeline have unified, secure access to multi-format web content.

## ADR-018: Spike S2 result, FTS5 tokenizer and weight bake-off is GO (Spike S2)

Context: D4 and D6 define the FTS5 full-text indexing configuration, BM25 column weights, phrase/NEAR/prefix operators, and WAL-mode concurrency. Before building Step 10 (FTS5 Index), Spike S2 evaluates tokenizers (`unicode61`, `porter unicode61 remove_diacritics 2`, `trigram`), BM25 weight presets, query operators, and concurrent readers during writes on a 30-document mini corpus with 15 hand-graded queries.
Findings (`spikes/s2_fts5`):
- Tokenizers:
  - `unicode61`: MRR = 0.933, nDCG@10 = 0.906. Lacks stemming and diacritic removal; completely fails on unspaced CJK.
  - `porter unicode61 remove_diacritics 2`: MRR = 1.000, nDCG@10 = 0.955. Stemming + diacritic folding provides 100% top-rank accuracy on European and accented languages (e.g. `café` -> `cafe`, `München` -> `munchen`). Fails on unspaced CJK.
  - `trigram`: MRR = 1.000, nDCG@10 = 0.962. Successfully handles unspaced CJK (e.g. Chinese `北京气候峰会`), exact substrings, and non-spaced languages.
- BM25 Column Weights:
  - D4 Default `(10.0, 5.0, 3.0, 1.0, 1.0)` on `(title, headings, description, body, url_tokens)` achieves MRR = 1.000 and nDCG@10 = 0.955, ensuring title matches reliably outrank body-only mentions.
- Query Operators:
  - Exact phrases (`"\"...\""`), proximity (`NEAR(..., 5)`), and prefix wildcards (`"..."*`) function deterministically without syntax errors.
- WAL Concurrency:
  - 100 writes and 250 reads executed simultaneously in WAL mode with `busy_timeout = 5000`.
  - 100% read success (250/250), 0 lock errors, read p50 = 0.32 ms, read p95 = 0.68 ms. Readers and writers never block each other.
Decision: GO. Adopt the dual-table FTS5 architecture per D4:
1. Primary index `doc_fts` with `tokenize = 'porter unicode61 remove_diacritics 2'`.
2. Trigram index `doc_fts_tri(title, body_prefix)` with `tokenize = 'trigram'` for CJK / non-spaced text and substring fallback.
3. Default BM25 column weights `(10.0, 5.0, 3.0, 1.0, 1.0)`.
Consequences: Step 10 will implement `src/search_engine/indexer/` based on these validated defaults.

## ADR-019: FTS5 index implementation and consistency verification (Step 10)

Context: Step 10 requires an inverted full-text index implementation per D4 with weighted BM25 retrieval, covering documents of type `page`, `pdf`, `text`, and `news`, with transaction-safe full rebuild, single-document upsert, document removal, deactivation, and index consistency verification.
Decision:
- Added migration `0002_fts_index.sql`:
  - `doc_fts(title, headings, description, body, url_tokens)` with `tokenize = 'porter unicode61 remove_diacritics 2'`.
  - `doc_fts_tri(title, body_prefix)` with `tokenize = 'trigram'`.
  - `doc_fts_vocab` virtual table using `fts5vocab(doc_fts, 'row')`.
  - Stored initial `index_version` in `index_state`.
- Implemented `src/search_engine/indexer/`:
  - `fts.py`: Weighted `bm25()` retrieval with default weights `(10.0, 5.0, 3.0, 1.0, 1.0)` ensuring title matches outrank body-only mentions; CJK character detection with automatic trigram routing and unspaced normalization; URL token extraction (`tokenize_url`); gzip text decompression.
  - `build.py`: Transaction-safe `rebuild_index` processing all active documents (`page`, `pdf`, `text`, `news`), tracking `IndexBuildReport`; `upsert_document_index` handling active documents and automatically removing unindexed statuses (`noindex`, `metadata_only`, `inactive`, `removed`, `unsupported`); `remove_document_index` and `deactivate_document`.
  - `consistency.py`: `verify_index_consistency` checking that every active document has exactly one row in both FTS tables, zero orphans exist, and internal FTS5 integrity passes.
- Verification: 15 unit tests in `tests/unit/test_fts_index.py` covering term retrieval, title vs body BM25 weighting, upsert text replacement, removal, deactivation, status rules (`noindex`, `metadata_only`, `unsupported`), multilingual indexing (French accents, German umlauts, Chinese CJK, Thai, Arabic), rebuild reproducibility from snapshot, fts5vocab population, and detection of deliberate index corruption. 419 project tests passed with 88% overall coverage.
Consequences: Query processing and CLI search (Step 11) can directly search the inverted index.

## ADR-020: Query processing, BM25 SearchService, and offline CLI demo (Step 11)

Context: Step 11 requires a thin vertical slice enabling offline end-to-end searching of the document corpus from the command line prior to crawler construction, conforming to Decision D5.
Decision:
- Implemented `src/search_engine/search/`:
  - `query.py`: Query normalization (NFKC, casefolding, control character stripping, whitespace collapsing), capping at 256 characters and 32 terms; phrase extraction with safe double-quote wrapping; stopword filtering for OR fallback (`DEFAULT_STOPWORDS`); CJK character detection; safe FTS5 MATCH expression builder.
  - `service.py`: `SearchService` executing BM25 retrieval over `doc_fts` (AND matching, falling back to OR with stopword removal when AND results < 5) and `doc_fts_tri` (for CJK queries). Returns `SearchResponse` with metadata (`title`, `url`, `score`, `doc_type`, `lang`, `description`, `word_count`, `published_at`) and reports `match_mode` (`and`, `or`, `trigram`, `empty`). Supports filtering by `doc_type` and `lang`.
- Added CLI commands in `src/search_engine/cli.py`:
  - `search-engine search "<query>"` with `--limit`, `--type`, `--lang`, `--json`, and `--db`.
  - `search-engine demo` executing an end-to-end offline pipeline: ingests the 20 checked-in HTML fixtures, builds the FTS5 index, and runs sample search queries.
- Updated `scripts/demo.ps1` and `Makefile` (`demo:` target) to execute `search-engine demo`.
- Verification:
  - 22 unit tests across `test_query.py`, `test_search_service.py`, and `test_cli_search.py`.
  - Hypothesis property fuzz testing with 100 random Unicode inputs proving that user input can never trigger an SQLite FTS5 MATCH syntax error.
  - 441 total project tests passed with 89% coverage and sockets blocked.
Consequences: Evaluation harness (Step 12) can measure search quality (P@5, P@10, MRR, nDCG@10) against graded queries.

## ADR-021: Search evaluation harness, designed corpus, and regression detection (Step 12)

Context: Step 12 establishes a repeatable quality evaluation harness before any ranking adjustments (Step 13) are made, ensuring we measure search quality (Precision@5, Precision@10, MRR, nDCG@10, and latency) and detect ranking regressions without touching held-out queries.
Decision:
- Designed fixture corpus (`src/search_engine/evaluation/corpus.py`):
  - 60 diverse documents covering databases, systems, ML/IR, clean energy, space, devops, and multilingual pages (French, German, Chinese, Japanese).
  - Explicit document characteristics: title matches vs body-only mentions, near-duplicates, stale vs fresh dates, short vs long text, and multiple document types (`page`, `pdf`, `text`, `news`).
- Evaluation datasets (`eval/`):
  - `eval/queries.csv`: 24 graded queries categorized across `exact`, `informational`, `news`, and `multilingual`, strictly partitioned into 16 `dev` queries and 8 `held_out` queries.
  - `eval/judgments.csv`: Graded relevance judgments on a 0-3 scale (78 judgments across queries).
- Evaluation engine (`src/search_engine/evaluation/`):
  - `evaluator.py`: Hand-verified metric implementations for `calculate_precision_at_k`, `calculate_mrr`, `calculate_dcg`, and `calculate_ndcg`. `evaluate_system` runs in-memory, enforces the dev vs held-out split, computes mean metrics, latency percentiles (p50, p95), zero-result rates, category breakdowns, and produces machine-readable JSON and Markdown reports.
  - `regression.py`: `check_regression` comparing against a saved baseline with configurable tolerance (default 0.05), flagging metric drops as regressions and gains as improvements.
  - `run_eval.py`: CLI evaluation runner with `--split`, `--queries`, `--judgments`, `--save-baseline`, `--check-baseline`, and `--tolerance`.
- CLI & scripts integration:
  - Added `search-engine eval` command in `src/search_engine/cli.py`.
  - Created `scripts/eval.ps1` and mirrored `eval:` target in `Makefile`.
  - Saved initial BM25 baseline to `eval/baseline.json` on the dev set (P@5 = 0.3125, P@10 = 0.2476, MRR = 1.0000, nDCG@10 = 0.9046, p50 latency = 0.25 ms).
- Verification:
  - 10 unit tests across `test_evaluation.py` and `test_cli_search.py`: hand-computed math verification for P@5, P@10, MRR, nDCG@10; deliberate ranking degradation detection; dev vs held-out split enforcement; corpus and judgment dataset integrity; 100% offline socket blocking; CLI invocation.
  - 451 total project tests passing with 88% overall test coverage.
Consequences: Step 13 can now implement ranking enhancements (freshness, source quality, title boost, phrase boost) and measure impact against the dev baseline while keeping held-out queries strictly untouched.

## ADR-022: Ranking signals, canonical grouping, host diversity, and did-you-mean suggestions (Step 13)

Context: Step 13 implements explainable, deterministic ranking and grouping per Decisions D5, D6, and D7, including bounded freshness with 24h news half-life, source-quality adjustments, title/phrase boosts, deterministic tie-breaking (score DESC, canonical URL ASC), canonical grouping, host diversity in the top 10, did-you-mean spelling suggestions via fts5vocab and rapidfuzz, and score explanations with per-signal switches for ablations.
Decision:
- Added runtime dependency `rapidfuzz>=3.14.0` (MIT license, approved fixed technology per BUILD_PLAN Section 4).
- Added `addopts = "--basetemp=.pytest_temp"` in `pyproject.toml` so pytest temporary directory cleanup never touches Windows protected system temp locations.
- Implemented `src/search_engine/search/ranking.py`:
  - `RankingConfig` with switches for every signal (`enable_freshness`, `enable_quality`, `enable_title_boost`, `enable_phrase_boost`, `enable_host_diversity`, `explain`).
  - `compute_freshness_multiplier`: Bounded to `[1.0, 1.10]` (+10% max). Requires real valid ISO date (future dates get no boost). 365-day half-life for standard pages; 24-hour half-life for news documents.
  - `compute_quality_multiplier`: Strictly bounded to `[0.95, 1.05]`. Penalizes content under 50 words (-0.03) and under 20 words (-0.05); bonuses for HTTPS (+0.02) and informative title length >= 10 (+0.02).
  - `compute_title_multiplier`: Exact title match (+15%) and all-terms match (+10%).
  - `compute_phrase_multiplier`: Multi-term query phrase match in title or description (+10%).
  - `ScoreExplanation`: Detailed breakdown of multipliers, base BM25 score, final score, and explainable reasons.
- Implemented `src/search_engine/search/grouping.py`:
  - `group_and_paginate`: Ingests candidate window (up to 200 candidates), groups by canonical URL or content hash, selects highest-scoring representative per group, applies host diversity in top 10 (at most 2 results per host; excess demoted, never dropped), and paginates over groups (max 20 per page, max 100 visible).
- Implemented `src/search_engine/search/suggest.py`:
  - `suggest_spelling`: Loads vocabulary from `doc_fts_vocab`. Uses `rapidfuzz.distance.DamerauLevenshtein` (edit distance <= 1 for terms < 5 chars, <= 2 for terms >= 5 chars) correcting single-character and transposition typos. Suppressed when search results are already solid. Triggers auto-correction banner ("Showing results for X; search instead for Y") only when 0 results and exactly 1 candidate at distance 1.
- Updated `SearchService`:
  - Integrated candidate retrieval, ranking signal scoring, canonical grouping, host diversity, and suggestions.
- Conducted ablation study on dev queries recorded in `docs/evaluation.md`:
  - All signals either improved or maintained baseline metrics.
  - Source quality improved nDCG@10 from 0.9046 to 0.9051.
  - Combined signals achieved 0.9051 nDCG@10, 1.0000 MRR, 0.3125 P@5, and 0.49 ms median latency.
  - Single end-of-step held-out measurement reported in `docs/evaluation.md`.
- Verification:
  - 10 unit tests in `test_ranking_grouping.py` testing title boost, bounded freshness with real dates, news 24h half-life, quality adjustment clamp, duplicate grouping, pagination disjointness and completeness, stable tie-breaking, single-character and transposition typos, and suppression of suggestions on good results.
  - 461 total project tests passing with 88% overall test coverage and sockets blocked.
Consequences: Step 14 can now implement snippet generation, query highlighting, and answer synopses for Milestone M1.

## ADR-023: Safe snippets, structured highlighting, and Milestone M1 (Step 14)

Context: Step 14 completes Milestone M1 by delivering query-biased best-passage snippet extraction, text highlighting, and a reusable sentence splitter, strictly adhering to security invariant I-16 (no HTML strings emitted by backend).
Decision:
- Implemented `src/search_engine/search/snippets.py`:
  - `HighlightSegment(text: str, is_match: bool)`: Structured data representation ensuring that HTML or malicious payload text (e.g. `<script>`, `<img src=x onerror=...>`) remains literal text and is never rendered as active HTML markup by the search API or backend.
  - `split_sentences`: Reusable, robust sentence boundary tokenizer handling English abbreviations (`Dr.`, `e.g.`, `i.e.`, `vs.`, `U.S.`, `p.m.`), decimals (`3.14`), version strings (`v0.1.0`), and CJK sentence terminators (`。`, `！`, `？`). Reusable in Step 21 answer synopsis.
  - `highlight_text`: Token-level matching that normalizes Unicode (NFKC) and strips diacritics while preserving original source text casing and characters.
  - `extract_snippet`: Query-biased passage scoring based on distinct matched terms, match frequency, proximity span, and position bias. Selects up to two non-overlapping passages joined by an ellipsis segment. Length is strictly bounded with word-boundary awareness. Provides fallback to document description or first sentence, and an explicit metadata-only label (`Metadata only. No body text available.`) when body text is absent.
- Integrated into `SearchService`:
  - Enriches paged search results with `title_highlight` and `snippet` segment lists, and plain text property `snippet_text`.
- Published Milestone M1 Quality Report in `docs/m1_quality_report.md`:
  - Zero duplicate results in top 10 (target 0 met).
  - Maximum results per host in top 10 <= 2 (target <= 2 met).
  - Snippet term coverage: 100%.
  - Zero socket leaks: 473 tests passing with `--disable-socket`.
- Verification:
  - 12 unit tests in `tests/unit/test_snippets.py` covering matching terms, bounded length, XSS payload neutralization, fallback snippets, CJK and accented text, long document scalability, metadata-only documents, determinism, and sentence splitting on abbreviations, initials, decimals, and lists.
  - 473 total project tests passing with 89% overall test coverage.
Consequences: Phase C (Content and Search) is fully verified and Milestone M1 is achieved. The engine is ready for Phase D (Crawler Pipeline & News Ingestion, Steps 15-18).

## ADR-024: Resumable SQLite Crawl Frontier (Step 15)

Context: Step 15 requires an independent SQLite-backed queue that decides what to crawl next safely and resumably, managing seed insertion, depth tracking, discovery sources (seeds, links, sitemaps, feeds, redirects), priority ordering, per-host grouping, per-host caps, global page/runtime limits, allowlist scope (D12), duplicate suppression by normalized key with depth lowering, and crash-safe lease timeouts.
Decision:
- Implemented `src/search_engine/crawler/frontier.py`:
  - `FrontierConfig`: Settings-driven configuration specifying `max_depth` (default 3), `max_pages` (default 500), `max_pages_per_host` (default 200), `lease_timeout_seconds` (default 300s), `max_retries` (default 3), `backoff_base_seconds` (2s), `backoff_max_seconds` (60s), `allowlist_mode` (D12), allowlist domains/hosts, and blocklists.
  - `calculate_priority`: Prioritizes seeds (100) > redirects (90) > sitemaps/feeds (80) > links by depth (`max(0, 50 - depth*10)`).
  - `Frontier.add_seed`: Normalizes URL, ensures host record, enforces blocklist/allowlist, adds seed at depth 0 with priority 100 in `pending` status. Re-adding an existing URL at depth 0 updates depth and priority without duplication.
  - `Frontier.add_discovered_url`: Handles links, sitemaps, feeds, and redirects. Records link relationships in `links` table. Merges duplicate URLs by normalized key: if rediscovered at a lower depth, updates depth and priority in `urls`, and restores URLs previously skipped due to depth limit back to `pending`.
  - Enforces policy gates: blocklisted URLs are marked `blocked`, non-allowlisted URLs are marked `skipped` (`outside_allowlist`), nofollow links are recorded in `links` and marked `skipped` (`nofollow`), depth > `max_depth` is marked `skipped` (`depth_exceeded`), and host/global caps prevent queueing. None of these reach `pending`.
  - `Frontier.lease_next`: Recovers expired leases automatically. Excludes active hosts and enforces per-host politeness by leasing at most one URL per host per batch. Selects candidates in `priority DESC, depth ASC, id ASC` order. Sets `lease_expires_at = now + lease_timeout`.
  - Transitions: `complete` (marks `done`, increments `hosts.pages_stored`), `retry` (exponential backoff `min(max, base * 2^(retries-1))`, transitions to `failed_permanent` when `max_retries` exceeded), `fail_permanent`, `skip`, and `block`.
  - Crash recovery & resumption: `recover_crashed_leases` resets expired `leased` URLs back to `pending`. `reset_leases` provides cold-start recovery across crawler process restarts.
  - Metrics: `stats` returns dictionary counts across all seven SQLite URL statuses (`pending`, `leased`, `done`, `retry`, `failed_permanent`, `blocked`, `skipped`).
- Verification:
  - 16 unit tests in `tests/unit/test_frontier.py` verifying seed insertion, duplicate merging with depth lowering, depth-limit restoration, per-host cap, global page cap, allowlist scope, blocklist filtering, nofollow link tracking, leasing priority, host politeness, retry exponential backoff transitions, mid-lease simulated crash recovery, and cold-start lease resets.
  - 489 total project tests passing with 88% overall test coverage and sockets blocked.
Consequences: The crawler frontier is resilient, bounded, and crash-safe. Step 16 can now connect the crawl pipeline with minimal safety controls (STOP file, disk space check, Windows-safe shutdown, reconciling run report).

## ADR-025: Controlled Crawl Pipeline with Safety Controls (Step 16)

Context: Step 16 connects the frontier, scheduler, SafeFetcher, content extraction adapters, atomic file storage, and FTS5 indexer into one bounded, stoppable, resumable crawl pipeline under strict safety invariants (D12 allowlist scope, D13 preflight authorization, loopback isolation, single choke point, structured RunReport).
Decision:
- Implemented `src/search_engine/crawler/preflight.py`:
  - `build_manifest`: Assembles canonical manifest of seeds, news sources, entity seeds, allowlist mode, user-agent, policy URL, news image policies, and all crawl/storage limits.
  - `compute_manifest_hash`: Computes SHA-256 over deterministic JSON representation.
  - `create_preflight_approval`: Operator command generating `data/preflight.approved.json`.
  - `verify_preflight_approval`: Mandatory gate for `crawl --mode real`. Verifies `real_network_enabled` is True and approval file exists and matches current hash in constant time.
- Implemented `src/search_engine/crawler/pipeline.py`:
  - `CrawlPipeline`: Bounded crawl coordinator managing the fetch loop across database and storage layers.
  - Minimal safety controls:
    - STOP file (`data/STOP`): Polled every loop; immediately halts pending work gracefully.
    - Signal handling: Windows-safe handlers for `SIGINT` (Ctrl+C), `SIGBREAK` (Ctrl+Break), and `SIGTERM` via `signal.signal`.
    - Disk space check: `shutil.disk_usage` guard checking `min_free_disk_bytes` (50 MiB threshold); halts and records `disk_low` before disk exhaustion.
    - Limits: Page count cap (`max_pages`), runtime cap (`max_runtime_seconds`), and storage cap (`max_storage_bytes`).
  - Crawl path execution:
    - Leased URLs submitted to `Scheduler.approve(url)`: enforces DNS, IP policy, robots.txt, politeness delays, and concurrency.
    - Politeness delay handling: gracefully waits up to allowed threshold or schedules backoff retry.
    - Approved URLs fetched through single choke point `SafeFetcher`.
    - Redirects returned as un-followed `Redirect` objects and re-submitted through the full approval path as `source="redirect"`.
    - Content extraction via `extract_content` adapter registry (HTML, text, PDF, XML sitemaps, RSS/Atom feeds).
    - Noindex meta directives stored in `documents` with status `noindex` and excluded from `doc_fts`.
    - Outgoing links discovered and queued into `Frontier` respecting `nofollow`.
    - Raw responses compressed and stored content-addressed via `FileStore`.
    - Clean resume: Resumes interrupted crawls without duplicate fetching by resetting crashed leases on cold start.
  - RunReport: Reconciles all processed URLs with exact event reasons (`ACCEPTED`, `STORED`, `NOINDEX`, `BLOCKED_ROBOTS`, `BLOCKED_POLICY`, `SKIPPED_LIMIT`, `SKIPPED_DUPLICATE`, `FETCHED`, `FAILED`, `UNSUPPORTED`).
- Extended CLI in `src/search_engine/cli.py`:
  - `search-engine crawl --mode fixture|real [--seeds PATH] [--limit N] [--time-limit S] [--json]`
  - `search-engine approve-preflight`
- Verification:
  - 8 comprehensive integration tests in `tests/unit/test_crawl_pipeline.py` testing end-to-end crawl on fixture server, robots-disallowed links, redirects through full approval path, max page limits, mixed content types (HTML, text, noindex), STOP file halting, kill-and-resume work preservation, and preflight authorization gating.
  - 2 CLI tests in `tests/unit/test_cli_search.py` verifying preflight approval generation and real mode refusal.
  - 499 total project tests passing with 88% overall test coverage and sockets blocked.
Consequences: A local site crawls end to end and the report explains every URL. Step 17 can now implement deduplication, canonical grouping, and freshness updates.












