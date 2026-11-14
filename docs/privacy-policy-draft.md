# Privacy Policy: Independent Search Engine
> **[DRAFT - PENDING OPERATOR LEGAL REVIEW]**
> *This document describes the privacy guarantees and technical controls implemented in the Independent Search Engine.*

---

## 1. Core Privacy Principles

The **Independent Search Engine** is built around complete local sovereignty, zero third-party telemetry, and minimal data retention.

- **Self-Hosted & Offline-First**: All search querying, ranking, grounded synopsis generation, autocomplete, and home page assembly happen entirely on your local machine.
- **Zero Third-Party Telemetry**: The application contains no Google Analytics, no tracking pixels, no remote font CDNs, and no external advertising networks.
- **No Cookies**: The web client and API server use zero cookies. No tracking cookies are stored on disk or sent in HTTP requests.

---

## 2. Query Privacy

- **Safe Query Logging**: By default (`log_raw_queries = false`), search queries are **never** logged to disk in raw text. Server logs record only:
  - Query character length
  - Result count
  - Match mode (AND, OR, TRIGRAM)
  - Execution latency in milliseconds
  - Salted hash of the client identifier / API key
- **No Query Association**: Queries are not correlated across sessions or linked to persistent user profiles.
- **Local Suggestion Index**: Autocomplete suggestions are precomputed entirely from document titles, news headlines, and vocabulary terms. Suggestion candidate lists are never generated from user query histories.

---

## 3. Data Storage & Retention

- **Transparent Storage**: All crawl state, inverted index tables, and news records reside in a single SQLite database file (`data/index/search.db`).
- **Hash-Addressed Raw Files**: Cached pages and images are stored addressed strictly by cryptographic SHA-256 hash (never by user info or raw URL paths).
- **Automated Retention**:
  - News items older than 14 days are automatically deactivated and purged from the search index.
  - Active news items are capped at 500 items, pruning the oldest first.
- **Operator Deletion**: Operators can purge all records for any URL or domain on demand:
  ```bash
  search-engine remove --domain example.com --purge-raw
  ```

---

## 4. Network Security & Local Bindings

- **Loopback Default**: The server binds exclusively to `127.0.0.1`.
- **Mandatory Authentication Beyond Loopback**: If configured to bind to a LAN or remote network interface, the server strictly refuses to start unless protected by a cryptographically strong API key and HTTPS/TLS.
- **Zero Outbound Serving Traffic**: The serving layer makes zero outbound internet connections (enforced by automated architecture tests and socket blocking).
