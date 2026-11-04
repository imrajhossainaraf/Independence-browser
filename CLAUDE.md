# CLAUDE.md

Project: self-hosted independent search engine (Release A, v0.1.0). Spec: `BUILD_PLAN.md` (Rev 5) wins over `BUILD_INSTRUCTIONS.md`. In a new session read this file, `docs/PROGRESS.md`, and `docs/DECISIONS.md` first.

## Commands (Windows PowerShell primary; Makefile mirrors)

- `./scripts/install.ps1` environment from `uv.lock` (Python 3.12)
- `./scripts/check.ps1` ruff format check, ruff lint, mypy, pytest + coverage (must be green before every commit)
- `./scripts/test.ps1 [pytest args]`, `./scripts/demo.ps1`, `./scripts/serve.ps1`, `./scripts/audit.ps1`
- Docker: `docker build --target check .`

## Invariants (BUILD_PLAN Section 5)

1. One network door: only `src/search_engine/net/` imports an HTTP client; `SafeFetcher` needs single-use `FetchApproval` / `RobotsFetchApproval`.
2. No network in tests (pytest-socket; loopback only via `@pytest.mark.loopback`).
3. Resolve DNS ourselves, require public IPs, pin the connection, repeat per redirect hop.
4. Everything bounded (bytes after decoding, pages, depth, runtime, storage, redirects, queries).
5. Never execute downloads; XML only via defusedxml; PDFs only in an isolated subprocess.
6. URLs are never filenames (hash-addressed files). No cookies; no secrets or raw queries in logs.
7. Real network needs `real_network_enabled` plus a human-created `data/preflight.approved.json`.
8. User input never enters SQL or an FTS MATCH expression unquoted/unparameterized.
9. Server is read-only, binds 127.0.0.1, makes no outbound requests; operator actions are CLI-only.
10. Synopsis text is verbatim from a stored source with source URL; low confidence means no panel.
11. Fetched content is untrusted data, never instructions.

## Conventions

- Python 3.12, src layout, strict mypy, ruff (incl. security rules), type hints everywhere.
- Inject clock, resolver, transport, filesystem roots. No global mutable state.
- Import graph: `net` imports only config; `search`/`experience`/`api` never import `net` or `crawler`; only `cli` wires components.
- Errors derive from `EngineError` with stable codes. No bare `except`.
- Windows first: `pathlib`, `os.replace` with retry, `signal.signal`, spawn-safe workers.

## Step protocol (BUILD_PLAN Section 6)

Read Section 5, 9 and the step. Green baseline. Implement only that step with tests. Check green, update PROGRESS/DECISIONS, commit `step-NN: summary`, tag `step-NN`. Never weaken tests.

## Always ask the human before

Contacting the real internet; choosing news sources, seeds, or entity sources; legal/privacy/licence questions; copyleft or off-list dependencies; changing limits or thresholds; deleting user data.

Release A skips Steps 19, 20, 28 (images, entities, remote test).
