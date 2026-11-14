# Independent Search Engine v0.1.0

A complete, self-hosted, offline-first search engine and news platform for one personal Windows computer. The whole engine lives on the server; the web page is a fast, responsive thin client that interacts exclusively with the read-only `/api/v1` REST API.

---

## Key Features

- **Ranked Full-Text Search**: SQLite FTS5 (Porter stemming + unicode61 diacritic removal) and FTS5 trigram indexing for CJK text and substring fallbacks.
- **High-Confidence Synopsis (Invariant 10)**: 100% verbatim grounded sentences extracted directly from top source documents, complete with source links and timestamps.
- **Local News Experience**: Ingests approved RSS/Atom feeds, clusters related stories with high precision, and maintains recent news on the home page with automated 14-day retention.
- **Safe & Polite Crawler**: Single choke point `SafeFetcher` with unforgeable single-use approvals, strict RFC 9309 robots compliance (Protego), DNS pinning, and subprocess-isolated PDF extraction.
- **Air-Gapped Real Crawling**: Live internet access requires explicit human preflight authorization (`data/preflight.approved.json`) and enforce hard ceilings.
- **Zero-Downtime Hot Backups**: Leverages SQLite Online Backup API accompanied by atomic snapshotting of hash-addressed raw storage.
- **Read-Only API Server**: Operator commands (crawl, poll, block, remove, backup) are CLI-only; HTTP endpoints are strictly read-only on `127.0.0.1`.

---

## Getting Started (Windows PowerShell)

### 1. Prerequisites
- **Windows 11** (or Windows 10)
- **PowerShell 5.1+**
- **uv** (fast Python package manager: install via `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`)

### 2. Quick Setup & Verification
Clone or enter the project directory and run:

```powershell
# Install all pinned dependencies from uv.lock into .venv
.\scripts\install.ps1

# Run the complete project verification suite (ruff format check, ruff lint, mypy, pytest with coverage)
.\scripts\check.ps1

# Initialize and run the offline vertical slice demo
.\scripts\demo.ps1
```

> **Execution Policy Note:** If Windows execution policy restricts running scripts, execute via:
> `powershell -ExecutionPolicy Bypass -File scripts\check.ps1`

---

## Daily Use Guide

### 1. Start the Web Server
Launch the read-only API server and web interface on `http://127.0.0.1:8000`:
```powershell
.\scripts\serve.ps1
```
Open your browser and navigate to `http://127.0.0.1:8000` to view the news home page and perform searches.

### 2. Search Offline via CLI
Search the indexed documents directly from PowerShell:
```powershell
uv run search-engine search "python"
```

### 3. Run the Automated 10-Point Daily-Use Checklist
Verifies all 10 core user journeys (home view, story link, search, synopsis grounding, sidebar source, offline search, empty query recovery, filters, suggestion follow-up, backup restore):
```powershell
.\scripts\daily-use.ps1
```

### 4. Ingest and Poll News Feeds
Poll configured RSS/Atom feeds (from `data/news_sources.txt`):
```powershell
.\scripts\news-poll.ps1
```

### 5. Create a Backup
Create an online hot backup of the database and raw storage:
```powershell
uv run search-engine backup --output-dir backups/
```

### 6. Restore from a Backup
Restore a backup snapshot into a target directory:
```powershell
uv run search-engine restore --backup-dir backups/<snapshot_id> --target-dir restored/
```

---

## Operator Controls & Safety

### Preflight Authorization for Real Crawls
Outbound web crawling remains air-gapped until the human operator verifies the preflight checklist and generates the authorization digest:
```powershell
# 1. Review and configure data/seeds.txt and data/news_sources.txt
# 2. Generate authorization manifest (writes data/preflight.approved.json)
uv run search-engine approve-preflight

# 3. Execute controlled crawl (hard ceiling 1,000 pages)
uv run search-engine crawl --mode real --set max_pages=10
```

### Operator Takedown & Blocking Commands
```powershell
# Block a domain, host, or URL prefix
uv run search-engine block example.com --reason "operator request"

# List active operator blocks
uv run search-engine blocks

# Remove a document from the index and database
uv run search-engine remove https://example.com/unwanted-page.html

# Emergency pause and stop
uv run search-engine pause    # creates data/PAUSE
uv run search-engine resume   # removes data/PAUSE
uv run search-engine stop     # creates data/STOP (clean shutdown)
```

---

## Documentation Index

- [Architecture Specification](docs/architecture.md): Deep-dive on subsystems, invariants, data flow, and concurrency.
- [Configuration Reference](docs/configuration.md): Complete list of all 72 settings, types, env vars, defaults, and hard ceilings.
- [Preflight & Staged Rollout Guide](docs/PREFLIGHT.md): Step-by-step checklist and commands for real-mode crawling.
- [Known Limitations](docs/known-limitations.md): Operational boundaries and intentional architectural constraints.
- [Release Notes v0.1.0](docs/release-notes-v0.1.0.md): Performance benchmarks, Section 7 gates, and feature overview.
- [Server Security Guide](docs/server-security.md): Host header validation, API keys, and network safety.
- [Crawler Policy](docs/crawler-policy.md): Politeness rules, user-agent specifications, and exclusion contacts.
- [Architectural Decisions (ADRs)](docs/DECISIONS.md): Complete log of architectural decisions.

---

## Release Verification Pipeline

Run the comprehensive release verification suite on a clean checkout:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\release-check.ps1
```
Or via make:
```bash
make release-check
```
