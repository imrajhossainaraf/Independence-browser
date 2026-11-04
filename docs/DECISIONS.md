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
