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


