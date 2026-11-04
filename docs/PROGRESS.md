# Progress

Release A (v0.1.0). Steps 19, 20, 28 are Release B and skipped.

| Step | Name | Status | Commit/tag | Gate |
| --- | --- | --- | --- | --- |
| 0 | Preflight | done | step-00 | approved by human |
| 1 | Skeleton, tooling, guardrails | done | step-01 | check green (24 tests, 92% cov); Docker check and runtime build pass |
| S1 | Spike: pinned-IP TLS | todo | | |
| 2 | Config, logging, reporting | todo | | |
| 3 | URL normalization and safety | todo | | |
| 4 | SQLite schema and storage | todo | | |
| 5 | Network safety, fixture server | todo | | |
| 6 | SafeFetcher | todo | | |
| 7 | Robots and scheduler | todo | | |
| 8 | HTML extraction | todo | | |
| 9 | Adapters | todo | | |
| 10 | FTS5 index | todo | | |
| 11 | Query processing, CLI search | todo | | |
| 12 | Evaluation harness | todo | | |
| 13 | Ranking and suggestions | todo | | |
| 14 | Snippets (M1) | todo | | |
| 15-17 | Frontier, crawl pipeline, dedup | todo | | |
| 18 | News ingestion | todo | | |
| 21 | Panels and home | todo | | |
| 22-23 | API and thin client (M2) | todo | | |
| 24-27 | Backup, controls, metrics, full eval | todo | | |
| 29 | Real collection (human controlled) | blocked on human | | |
| 30 | Package v0.1.0 | todo | | |

## Working mode

The human waived per-step approval stops ("deliver the full finished project"). Steps still get their own commit and tag. Work still stops for: real internet access, choosing sources/seeds, legal/licence decisions, off-list dependencies, threshold changes (see CLAUDE.md).

## Open questions

- Step 29 needs the human: focus topics, seed sites, news feeds, published crawler-policy page, legal review, `search-engine approve-preflight`.
