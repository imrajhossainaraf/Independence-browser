# Progress

Release A (v0.1.0). Steps 19, 20, 28 are Release B and skipped.

| Step | Name | Status | Commit/tag | Gate |
| --- | --- | --- | --- | --- |
| 0 | Preflight | done | step-00 | approved by human |
| 1 | Skeleton, tooling, guardrails | done | step-01 | check green (24 tests, 92% cov); Docker check and runtime build pass |
| S1 | Spike: pinned-IP TLS | done (GO) | spike-s1 | 8 tests; keep-alive hazard found, fix in Step 5 (ADR-006) |
| 2 | Config, logging, reporting | done | step-02 | 86 tests, 98% cov; ceilings, redaction, report tests pass |
| 3 | URL normalization and safety | done | step-03 | 265 tests, 98% cov; every D1/D2 rule has a table row; hypothesis fuzz and idempotence pass |
| 4 | SQLite schema and storage | done | step-04 | 295 tests, 97% coverage |
| 5 | Network safety, fixture server | done | step-05 | 320 tests, 97% coverage; resolver, transport, IP gate, fixture server |
| 6 | SafeFetcher | done | step-06 | 347 tests, 97% coverage; single choke point, approvals, streaming caps, magic-byte sniffing |
| 7 | Robots and scheduler | done | step-07 | 360 tests, 96% coverage; Protego adapter, robots cache, Scheduler.approve, concurrency & politeness |
| S5 | Spike: text extraction bake-off | done (GO) | spike-s5 | 20 fixtures, combined avg F1 0.993, min F1 0.953, 0 leakage (ADR-014) |
| 8 | HTML extraction | done | step-08 | 378 tests, 94% coverage; deterministic extraction, models, 20 gold fixtures F1 0.993, 0 leakage (ADR-015) |
| S3 | Spike: subprocess isolation | done (GO) | spike-s3 | Windows Job Object + psutil monitoring, timeout, memory cap, tree kill (ADR-016) |
| 9 | Adapters | done | step-09 | 400 tests, 89% coverage; plain text, PDF isolated worker, RSS/Atom, sitemaps (ADR-017) |
| S2 | Spike: FTS5 tokenizer bake-off | done (GO) | spike-s2 | 30 docs, 15 queries; porter unicode61 + trigram confirmed, WAL p95 0.68ms (ADR-018) |
| 10 | FTS5 index | done | step-10 | 419 tests, 88% coverage; doc_fts + doc_fts_tri, bm25 weighting, rebuild, consistency (ADR-019) |
| 11 | Query processing, CLI search | done | step-11 | 441 tests, 89% cov; D5 safe quoting, AND/OR fallback, SearchService, demo.ps1 (ADR-020) |
| 12 | Evaluation harness | done | step-12 | 451 tests, 88% cov; 60-doc corpus, 24 queries (16 dev, 8 held-out), baseline.json, regression detection (ADR-021) |
| 13 | Ranking and suggestions | done | step-13 | 461 tests, 88% cov; D5/D6/D7 ranking, grouping, host diversity, did-you-mean, ablation table in docs/evaluation.md (ADR-022) |
| 14 | Snippets (M1) | done | step-14 | 473 tests, 89% cov; Milestone M1 achieved, safe snippets, sentence splitter, quality report in docs/m1_quality_report.md (ADR-023) |
| 15 | Frontier | done | step-15 | 489 tests, 88% cov; SQLite-backed queue, leases, crash recovery, retries, host caps, allowlist/blocklist (ADR-024) |
| 16 | Controlled crawl pipeline | done | step-16 | 499 tests, 88% cov; pipeline, loopback link extraction, preflight manifest (ADR-025) |
| 17 | Deduplication and freshness | done | step-17 | 508 tests, 87% cov; exact hash, canonical grouping, 304, 404 deactivation (ADR-026) |
| S4 | Spike: poller, server, Task Scheduler | done (GO) | spike-s4 | WAL p95 2.15ms, ProcessLock crash recovery, Task Scheduler documented (ADR-027) |
| 18 | News ingestion | done | step-18 | 531 tests, 88% cov; ProcessLock, poller, clustering, retention, 304 caching, Task Scheduler documented (ADR-028) |
| 21 | Panels and home | done | step-21 | 541 tests, 87% cov; Grounded synopsis (Invariant 10), preview/entity cards, recent news, home payload, autocomplete (ADR-029) |
| 22 | API-first search server | done | step-22 | 555 tests, 86% cov; Versioned read-only /api/v1 (health, home, news, search, images, suggest), OpenAPI 3.1 contract, host/origin/rate hardening, server-security & threat model (ADR-030) |
| 23 | Thin web client (M2) | done | step-23 | 561 tests, 86% cov; Milestone M2 achieved: static HTML/CSS/JS client, strict textContent XSS safety, WAI-ARIA combobox, responsive layout, WCAG 2.2 AA (ADR-031) |
| 24 | Backup, restore, offline mode | done | step-24 | 573 tests, 86% cov; Online backup (Connection.backup), manifest hashing, integrity checks, index consistency verification, safe restore to empty target, offline serving (ADR-032) |
| 25 | Operator controls & policy | done | step-25 | 584 tests, 86% cov; Blocklist, document/news/image/entity removal, pause/resume/stop, drive audit, retention purge, legal/policy docs (ADR-033) |
| 26 | Metrics & diagnostic reports | done | step-26 | 589 tests, 86% cov; Metrics persistence, percentile calculations, diagnostic gatherers, CLI report command (ADR-034) |
| 27 | Full evaluation & benchmarks | done | step-27 | 596 tests, 86% cov; 58 queries, held-out P@5 0.66, MRR 0.92, nDCG@10 0.83, 10-point daily-use checklist, Section 7 benchmarks (ADR-035) |
| 29 | Real collection preflight & safety | prepared / locked | step-29 | 602 tests, 85% cov; Preflight guide, manifest tamper detection, seeds/news templates, STOP/PAUSE rehearsal (ADR-036) |
| 30 | Package v0.1.0 | done | step-30 / v0.1.0 | 607 tests, 85% cov; Version 0.1.0, release-check.ps1, architecture.md, known-limitations.md, release notes, configuration reference (ADR-037) |

## Working mode

The human waived per-step approval stops ("deliver the full finished project"). Steps still get their own commit and tag. Work still stops for: real internet access, choosing sources/seeds, legal/licence decisions, off-list dependencies, threshold changes (see CLAUDE.md).

## Open questions

- Step 29 needs the human: focus topics, seed sites, news feeds, published crawler-policy page, legal review, `search-engine approve-preflight`.
