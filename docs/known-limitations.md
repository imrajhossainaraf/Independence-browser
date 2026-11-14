# Known Limitations: Independent Search Engine v0.1.0

This document outlines the architectural boundaries, intentional trade-offs, and known operational limitations of Independent Search Engine in the v0.1.0 release.

---

## 1. Single-Machine Architecture

- **Localhost Scope**: The engine is designed to run on a single personal computer (Windows 11 with Python 3.12+). Distributed crawling, multi-node clustering, and sharded indexes are out of scope for Release A.
- **SQLite Single-Writer Model**: SQLite WAL mode enables unlimited concurrent readers alongside a single active writer. The crawler and news poller must not write concurrently; background polling jobs should be scheduled when crawls are not executing.

---

## 2. Crawl & Extraction Limitations

- **No JavaScript Execution**:
  - The crawler does not include a headless browser (such as Chromium or Playwright).
  - Single-Page Applications (SPAs) that require client-side JavaScript to render their primary text will yield minimal or empty document extractions.
- **Hard Resource Ceilings**:
  - Page limit is capped at a hard maximum of 1,000 pages.
  - Streaming responses are capped at 2 MiB for HTML, text, and feeds, and 5 MiB for PDFs. Pages exceeding these limits are truncated or rejected with reason code `skipped_limit`.
- **PDF Extraction Depth**:
  - PDF extraction uses `pypdf` in a sandboxed subprocess with a 10-second timeout and 50-page maximum. Complex vector graphics, password-protected PDFs, and scanned image-only PDFs (lacking text layers) are not OCR'd.
- **Air-Gapped Real Crawling**:
  - By design, the engine refuses all live internet access until the human operator generates `data/preflight.approved.json` via `search-engine approve-preflight`.

---

## 3. Search & Ranking Limitations

- **In-Memory Candidate Pool**:
  - For performance and memory safety (under 300 MB serving RSS), the ranking pipeline evaluates the top 200 candidates retrieved from the SQLite FTS5 index before applying proximity scoring, freshness decay, and canonical deduplication.
- **No External Search APIs**:
  - The engine relies exclusively on locally crawled and indexed documents. It does not query third-party search APIs (e.g. Google, Bing, DuckDuckGo). Queries outside the local index return zero results.
- **Verbatim Synopsis Strictness (Invariant 10)**:
  - Synopsis panels extract only 100% verbatim sentences directly from the single top-ranking document. The engine will not synthesize, generate, or summarize text across multiple documents. If query term coverage falls below 60%, no panel is displayed.
- **Language Tokenization**:
  - Latin and European languages utilize the Porter stemmer with diacritic removal. CJK (Chinese, Japanese, Korean) text utilizes the FTS5 trigram tokenizer. Morphological segmentation (e.g. MeCab, Jieba) is planned for future releases.

---

## 4. News Experience

- **14-Day Retention**:
  - Stored news items are automatically retained for at most 14 days and capped at 500 total items.
- **Feed Dependent**:
  - News summaries are limited to the text provided in RSS/Atom feeds (capped at 300 characters). Full-text extraction of news article bodies is reserved for full web crawls.

---

## 5. Release B Deferred Capabilities

The following features are designed for Release B (v0.2.0) and remain disabled in v0.1.0:

1. **Image Search & Thumbnails**: Full image pipeline, WebP thumbnail generation, and image search (Step 19).
2. **Entity Knowledge Graph**: Sidebar entity cards from Wikidata and Wikipedia (Step 20).
3. **Query Autocomplete**: In-memory prefix suggestions (Step 21).
4. **Remote Access / Private Tunnel**: Remote device testing via Tailscale / trusted LAN (Step 28).
