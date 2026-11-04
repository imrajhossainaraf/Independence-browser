# Preflight Report (Step 0)

Date: 2026-11-04. Documentation only. No application code written, no website contacted.

## Machine

| Item | Result |
| --- | --- |
| OS | Windows 11 Pro 10.0.26300 |
| PowerShell | Windows PowerShell 5.1 (Desktop) |
| Execution policy | LocalMachine = RemoteSigned, others undefined (local scripts run; fallback `powershell -ExecutionPolicy Bypass -File`) |
| Long paths | Enabled (registry `LongPathsEnabled = 1`); project still keeps data paths under 260 characters |
| CPU | Intel Core i5-1334U, 10 cores, 12 threads |
| RAM | 7.7 GB (memory targets of 300 MB serving and 600 MB crawling are realistic) |
| Free disk (C:) | 39 GB |
| Python | 3.14.7 at `C:\Python314\python.exe` |
| `py` launcher | Broken: points to a missing path under another user profile. Use `python` directly |
| git | 2.53.0 (no repository exists yet in the project folder) |
| uv | Not installed |
| Docker | Docker CLI 29.8.0; daemon reachable (Linux engine) |
| GitHub CLI (`gh`) | Not installed |
| venv creation | Works (tested in a temp folder and deleted) |

## SQLite (throwaway in-memory test)

| Feature | Result |
| --- | --- |
| SQLite library | 3.50.4 |
| FTS5 with `porter unicode61 remove_diacritics 2` | OK |
| fts5vocab | OK |
| trigram tokenizer (substring match verified) | OK |
| `bm25()` weighted ranking | OK |
| WAL mode | Requires a file database (in-memory reports `memory`, expected); verified on disk in Step 4 |

Gate for Release A (FTS5 and trigram present): **passed**.

## Not checked

- Pillow WebP support: only needed for Release B images. Deferred to Release B.

## Goal, non-goals, risks

- Goal: self-hosted search engine on one Windows laptop; news home page, ranked search, snippets, grounded synopsis, preview card, read-only `/api/v1`, thin web client.
- Non-goals: generative text, JS rendering, accounts, external search APIs, public hosting, and (Release B) images, entities, autocomplete, remote testing.
- Risks:
  1. Python 3.14 is very new; wheels for `selectolax`, `trafilatura`, `pypdf` may lag. Mitigation: pin Python 3.12 via uv and in Docker.
  2. Pinned-IP TLS with httpx (Spike S1) is the hardest piece.
  3. 7.7 GB RAM leaves limited headroom beside Docker Desktop.
  4. The broken `py` launcher can confuse scripts; scripts will use `uv run` or the venv python.

## Docker and CI/CD (added at your request, not in BUILD_PLAN.md)

BUILD_PLAN.md Section 7A lists CI as optional and has no Docker requirement. Proposal, to be built in Step 1 after your approval:

- `Dockerfile` (Python 3.12 slim, uv, non-root user, no network needed at runtime) and `.dockerignore`.
- A Docker-based run of `check` so the build is reproducible: `docker build --target check`.
- GitHub Actions workflow `.github/workflows/ci.yml`: Windows and Linux jobs running ruff, mypy, pytest, plus pip-audit and bandit before tags. Needs a GitHub remote (you create it).
- Windows stays authoritative; the container serves Linux compatibility. The server in the container would bind `127.0.0.1` inside the container only; any published port is bound to host `127.0.0.1`.

## Decisions needing your approval

1. Install `uv` (needed by the plan).
2. Use Python 3.12 via uv instead of the installed 3.14.
3. Approve Docker and GitHub Actions as an addition to the plan (record in DECISIONS.md).
4. Do you have, or will you create, a GitHub repository for CI?
5. Appendix C decisions (news sources, seeds, legal review) are not needed until Step 29.

## Overwrite check

Project folder contained only BUILD_INSTRUCTIONS.md and BUILD_PLAN.md. Nothing existing was changed.
