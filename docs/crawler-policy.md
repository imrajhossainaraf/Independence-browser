# Crawler Policy: Independent Search Engine
> **[DRAFT - PENDING OPERATOR LEGAL AND SITE-POLICY REVIEW]**
> *This document is a template and draft intended for human review prior to any real-network web crawling.*

---

## 1. Bot Identity and Purpose

The **Independent Search Engine** crawler operates strictly as a self-hosted, personal research and indexing tool designed for focused, polite web discovery.

- **User-Agent String**:
  ```text
  IndependentSearchBot/0.1.0 (+https://example.org/crawler-policy)
  ```
  The full User-Agent header always includes the bot name, version, and a reachable policy/contact URL where site owners can find operator contact information and exclusion instructions.
- **Operator Contact**:
  Operators must configure their real contact email or policy URL in `SEARCH_ENGINE_POLICY_URL` or `.env` before enabling real network crawls.

---

## 2. Respect for Standards & Robots.txt (RFC 9309)

The crawler strictly conforms to RFC 9309 (Robots Exclusion Protocol):
1. **Robots Cache Verification**:
   - Before fetching any URL on a host, the crawler requests `<scheme>://<host>:<port>/robots.txt` through a dedicated `RobotsFetchApproval`.
   - Robots rules are cached for at most 24 hours (transient network failures cached for at most 10 minutes).
2. **Strict Disallow Conformance**:
   - If a target URL matches a `Disallow:` path for the bot token (or `*`), it is immediately refused and marked `blocked_robots`.
   - HTTP 5xx errors or network timeouts on `robots.txt` fail closed (treating the entire host as disallowed).
3. **Crawl-Delay & Politeness**:
   - `Crawl-delay:` directives up to 30 seconds are strictly honored.
   - If a host requests a delay exceeding the configured ceiling (30 seconds), the host is skipped entirely to avoid excessive resource usage.

---

## 3. Strict Concurrency & Rate Limits

To prevent any noticeable load on host servers:
- **Per-Host Concurrency**: Strictly **1 connection per host** at any time.
- **Per-Host Politeness Delay**: Minimum 1.0 second between consecutive requests to the same host (or higher if specified by `Crawl-delay`).
- **Global Concurrency**: Capped at 8 concurrent connections globally.
- **Exponential Backoff**: If a host returns HTTP 429 (Too Many Requests) or 503 (Service Unavailable), the crawler immediately backs off exponentially (`backoff_base_seconds` * 2^retries up to 60s).

---

## 4. Bounded Resource Limits

The crawler enforces hard ceilings on every transfer:
- **Max Content Bytes**:
  - HTML, plain text, and RSS/Atom feeds: 2 MiB decoded.
  - PDF documents: 5 MiB decoded (capped at 50 pages).
  - Images: 2 MiB encoded.
- **Streaming Limit Enforcement**: Bytes are measured while streaming from the socket; if a response exceeds the byte cap, the connection is aborted immediately.
- **Maximum Pages**: Default 500 pages per crawl run (hard ceiling 1,000 pages).
- **Maximum Depth**: Default depth 3 from seed URLs.

---

## 5. Security & Network Boundary Guardrails

- **Single Choke Point (`SafeFetcher`)**: All outbound HTTP traffic flows through a single audited choke point.
- **Pinned-IP TLS Connections**: Every hostname is pre-resolved to public IP addresses only. Private, loopback, link-local, carrier-grade NAT, or multicast IPs are rejected at the socket layer (Invariant 3).
- **Zero Script Execution**: Downloaded content is never rendered in a headless browser and never executed. SVG images and animated GIFs are rejected.
- **No Cookies**: The crawler never sends, accepts, or stores HTTP cookies.

---

## 6. How to Request Exclusion or Takedown

If you are a webmaster or content owner and do not wish your site to be indexed:
1. **Robots.txt Exclusion**:
   Add the following directive to your `robots.txt`:
   ```text
   User-agent: IndependentSearchBot
   Disallow: /
   ```
2. **Direct Operator Request**:
   Contact the operator at their published policy URL. Operators can immediately execute removal via CLI:
   ```bash
   search-engine remove --domain yourdomain.com --purge-raw
   search-engine block domain yourdomain.com
   ```
   This immediately deletes all search index rows and removes stored raw files.
