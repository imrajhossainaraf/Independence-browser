# Threat Model & Security Controls

This document tracks all recognized threats, entry points, mitigations, and automated verification tests per Appendix E of the Build Plan.

## Assets
- The local database and data directory (`data/`): raw documents, inverted index, image thumbnails, news items, and entities.
- Secret credentials: API keys, authorization tokens.
- Laptop system resources: CPU, memory, disk, network sockets.
- Crawler reputation: politeness, respect for robots.txt, avoiding accidental denial of service on remote hosts.

## Threat Matrix

| ID | Threat | Entry point | Control | Test |
| --- | --- | --- | --- | --- |
| **T1** | SSRF through URLs, redirects, or DNS rebinding | Crawl and feed URLs | D2 URL normalization, IP policy, pinned-address transport | `test_net_safety.py`, `test_ip_policy.py`, `test_fetcher.py` |
| **T2** | Malicious page, feed, or entity content (XSS) | Fetched HTML, RSS/Atom feeds, search queries | Invariant 12, strict `textContent` rendering, Content Security Policy (`default-src 'none'`), no HTML string interpolation | `test_snippets.py`, `test_api_server.py`, `test_client_smoke.py` |
| **T3** | XML entity expansion and billion-laughs bombs | Feeds, XML sitemaps | `defusedxml` parser with entity resolution disabled and size caps | `test_adapters.py` (feed and sitemap attack fixtures) |
| **T4** | Decompression bombs | HTTP responses, gzip raw storage, compressed sitemaps | Streaming decoded-size limits in `SafeFetcher` and adapter byte caps | `test_fetcher.py`, `test_adapters.py` |
| **T5** | Parser or decoder exploits | PDF documents, complex binaries | Out-of-process worker isolation with CPU timeout, memory limits, and process-tree termination | `test_adapters.py`, `test_s3.py` |
| **T6** | Path traversal attacks | Image IDs, document URLs, backup/restore paths | Opaque IDs, SHA-256 hash-addressed storage, restore only into clean empty directories | `test_db_storage.py`, `test_api_server.py` |
| **T7** | Credential leakage in logs or error messages | HTTP API headers, crawler requests, exceptions | Redaction filters in `logs.py`, hashed key storage in database, sanitized error responses | `test_logs.py`, `test_api_server.py` |
| **T8** | API key brute-forcing or abuse | HTTP API `/api/v1/*` | 256-bit keys, sliding-window rate limits (60 rpm default), constant-time string comparison (`hmac.compare_digest`) | `test_api_server.py` |
| **T9** | DNS rebinding against the local server | Browser scripts on malicious websites accessing loopback | Host header allowlist enforcement via `HostHeaderMiddleware` | `test_api_server.py` |
| **T10** | Cross-Origin Resource Sharing (CORS) abuse | Malicious web pages fetching local data | CORS disabled by default; exact configured origins only when enabled; wildcard `*` strictly forbidden | `test_api_server.py`, `test_config.py` |
| **T11** | Query-based denial of service | Search and autocomplete endpoints | Query length limit (256 chars), page depth cap, rate limits, statement timeouts | `test_api_server.py`, `test_query.py` |
| **T12** | SQL and FTS syntax injection | Query inputs, search parameters | Parameterized SQL everywhere, sanitized token quoting in FTS queries, Unicode fuzzing | `test_query.py`, `test_fts_index.py` |
| **T13** | Prompt injection into AI agents | Fetched website content | Invariant 12: external web content treated strictly as passive data, never instructions | Architecture tests, all extraction tests |
| **T14** | Supply-chain compromise | Python packages and dependencies | Committed lockfile with hashes (`uv.lock`), minimum 14-day release maturity rule, `pip-audit`, Bandit static analysis | `scripts/audit.ps1`, `test_architecture.py` |
| **T15** | Accidental public server exposure | Network interface binding | Bound to `127.0.0.1` by default; startup refusal on non-loopback bind without an API key | `test_api_server.py` |
| **T16** | Data corruption or tampering | Local storage files and SQLite database | Content-addressed SHA-256 hashes, SQLite WAL mode with integrity checks, verified backup manifests | `test_db_storage.py`, `test_fts_index.py` |
| **T17** | Content removal / takedown requests | Published articles, thumbnails, documents | CLI removal primitives (`remove --url`, `remove --domain`, `news remove-item`, `images takedown`) with soft-deletion and FTS de-indexing | `test_cli_search.py`, `test_dedup_freshness.py` |
