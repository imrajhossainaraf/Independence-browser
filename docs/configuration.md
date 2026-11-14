# Configuration Reference: Independent Search Engine v0.1.0

This reference documents the configuration system, precedence rules, hard safety ceilings, environment variables, and all 72 settings supported by Independent Search Engine.

---

## 1. Precedence Order

Configuration is loaded from four hierarchical layers (lowest to highest precedence):

1. **Built-in Defaults**: Hardcoded secure defaults defined in `src/search_engine/config.py`.
2. **Configuration File**: A TOML configuration file passed via `--config-file <path>` (e.g. `config/production.toml`).
3. **Environment Variables**: Variables prefixed with `SEARCH_ENGINE_` (e.g. `SEARCH_ENGINE_SERVER_PORT=8080`).
4. **Command-Line Overrides**: Explicit CLI flags or `--set KEY=VALUE` arguments (highest precedence).

### Inspecting Configuration
To view the effective configuration with source attribution and secret redaction:
```powershell
uv run search-engine config show
```

---

## 2. Safety Invariants & Hard Ceilings

The following constraints are hardcoded into the validator and **cannot be overridden** by configuration or environment variables:

| Parameter | Hard Ceiling / Rule | Rationale |
| :--- | :--- | :--- |
| `max_pages` | Maximum 1,000 pages | S5 / Section 3 cap for single-machine deployment |
| `max_depth` | Maximum 5 | Prevents spider trapping |
| `max_html_bytes` | Maximum 2 MiB (2,097,152 bytes) | Prevents memory exhaustion from giant responses |
| `max_pdf_bytes` | Maximum 5 MiB (5,242,880 bytes) | Prevents decompression bombs in PDF extraction |
| `max_storage_bytes` | Maximum 1 GiB (1,073,741,824 bytes) | Hard ceiling on raw storage directory |
| `real_network_enabled` & `allow_loopback_for_tests` | **Mutually exclusive** | Real network mode can never access loopback or private IPs |
| `server_host` | Default `127.0.0.1` | Non-loopback bind strictly requires `api_auth_required=true` and valid API key |

---

## 3. Configuration Settings Reference

### 3.1. Engine Identity & User-Agent
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `user_agent_token` | `SEARCH_ENGINE_USER_AGENT_TOKEN` | string | `IndependentSearchBot` | Product token placed in the User-Agent header |
| `policy_url` | `SEARCH_ENGINE_POLICY_URL` | string | `""` | Reachable URL containing operator contact and bot policy (D14) |

### 3.2. Input Files & Seed Configuration
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `seeds_file` | `SEARCH_ENGINE_SEEDS_FILE` | Path | `data/seeds.txt` | Path to seed URLs file |
| `news_sources_file` | `SEARCH_ENGINE_NEWS_SOURCES_FILE` | Path | `data/news_sources.txt` | Path to approved RSS/Atom feeds list |
| `entities_seed_file` | `SEARCH_ENGINE_ENTITIES_SEED_FILE` | Path | `data/entities_seed.txt` | Path to entity seed titles list (Release B) |
| `allowlist_mode` | `SEARCH_ENGINE_ALLOWLIST_MODE` | bool | `true` | Restrict crawls to registered domains present in seeds (D12) |

### 3.3. Crawl Scope & Limits
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `max_pages` | `SEARCH_ENGINE_MAX_PAGES` | int | `500` | Target page cap (hard ceiling: 1,000) |
| `max_depth` | `SEARCH_ENGINE_MAX_DEPTH` | int | `3` | Maximum crawl depth from seeds (hard ceiling: 5) |
| `max_pages_per_host` | `SEARCH_ENGINE_MAX_PAGES_PER_HOST` | int | `200` | Maximum pages fetched per registered host |
| `recrawl_after_days` | `SEARCH_ENGINE_RECRAWL_AFTER_DAYS` | int | `7` | Minimum interval before recrawling an existing active URL |

### 3.4. Content Types & Byte Limits (Post-Decompression)
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `max_html_bytes` | `SEARCH_ENGINE_MAX_HTML_BYTES` | int | `2097152` | Streaming decoded byte limit for HTML responses (2 MiB) |
| `max_text_bytes` | `SEARCH_ENGINE_MAX_TEXT_BYTES` | int | `2097152` | Streaming decoded byte limit for text responses (2 MiB) |
| `max_feed_bytes` | `SEARCH_ENGINE_MAX_FEED_BYTES` | int | `2097152` | Streaming decoded byte limit for feeds (2 MiB) |
| `max_pdf_bytes` | `SEARCH_ENGINE_MAX_PDF_BYTES` | int | `5242880` | Maximum PDF file size limit (5 MiB) |
| `max_sitemap_bytes` | `SEARCH_ENGINE_MAX_SITEMAP_BYTES` | int | `5242880` | Maximum sitemap file size limit (5 MiB) |
| `max_image_bytes` | `SEARCH_ENGINE_MAX_IMAGE_BYTES` | int | `2097152` | Encoded image size cap before resizing (2 MiB) |
| `allowed_content_types` | `SEARCH_ENGINE_ALLOWED_CONTENT_TYPES` | tuple | `text/html,...` | MIME types accepted by crawler and fetcher |
| `max_redirects` | `SEARCH_ENGINE_MAX_REDIRECTS` | int | `5` | Maximum redirect hops followed before aborting |
| `max_storage_bytes` | `SEARCH_ENGINE_MAX_STORAGE_BYTES` | int | `1073741824` | Total storage byte limit for raw files (1 GiB) |
| `max_runtime_seconds`| `SEARCH_ENGINE_MAX_RUNTIME_SECONDS` | int | `3600` | Maximum continuous crawl execution time (1 hour) |
| `min_free_disk_bytes`| `SEARCH_ENGINE_MIN_FREE_DISK_BYTES` | int | `1073741824` | Minimum free disk space before crawl pauses (1 GiB) |

### 3.5. Timeouts & Politeness
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `connect_timeout_seconds` | `SEARCH_ENGINE_CONNECT_TIMEOUT_SECONDS` | float | `10.0` | Socket connect timeout in seconds |
| `read_timeout_seconds` | `SEARCH_ENGINE_READ_TIMEOUT_SECONDS` | float | `20.0` | Socket read timeout in seconds |
| `dns_timeout_seconds` | `SEARCH_ENGINE_DNS_TIMEOUT_SECONDS` | float | `5.0` | DNS resolution timeout in seconds |
| `per_host_delay_seconds` | `SEARCH_ENGINE_PER_HOST_DELAY_SECONDS` | float | `1.0` | Minimum delay between consecutive requests to same host |
| `max_crawl_delay_seconds`| `SEARCH_ENGINE_MAX_CRAWL_DELAY_SECONDS` | float | `30.0` | Maximum robots.txt `Crawl-delay` honored before skipping host |
| `per_host_concurrency` | `SEARCH_ENGINE_PER_HOST_CONCURRENCY` | int | `1` | Strict concurrency cap per host |
| `global_concurrency` | `SEARCH_ENGINE_GLOBAL_CONCURRENCY` | int | `8` | Maximum concurrent requests across all hosts |
| `max_retries` | `SEARCH_ENGINE_MAX_RETRIES` | int | `3` | Maximum retry attempts for transient HTTP / network errors |
| `backoff_base_seconds` | `SEARCH_ENGINE_BACKOFF_BASE_SECONDS` | float | `2.0` | Exponential backoff base in seconds |
| `backoff_max_seconds` | `SEARCH_ENGINE_BACKOFF_MAX_SECONDS` | float | `60.0` | Maximum capped backoff delay |

### 3.6. Storage & Paths
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `db_path` | `SEARCH_ENGINE_DB_PATH` | Path | `data/index/search.db` | SQLite database file path (WAL mode) |
| `raw_dir` | `SEARCH_ENGINE_RAW_DIR` | Path | `data/raw` | Hash-addressed gzip document storage directory |
| `image_dir` | `SEARCH_ENGINE_IMAGE_DIR` | Path | `data/images` | Hash-addressed WebP thumbnail storage directory |
| `log_dir` | `SEARCH_ENGINE_LOG_DIR` | Path | `data/logs` | Structured JSON log directory |
| `image_max_files` | `SEARCH_ENGINE_IMAGE_MAX_FILES` | int | `1500` | Maximum number of stored image files (Release B) |
| `image_max_total_bytes` | `SEARCH_ENGINE_IMAGE_MAX_TOTAL_BYTES` | int | `157286400` | Maximum total bytes for image cache (150 MB) |
| `entity_max` | `SEARCH_ENGINE_ENTITY_MAX` | int | `2000` | Maximum entity records allowed (Release B) |

### 3.7. Operational Modes & Safety Flags
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `development_mode` | `SEARCH_ENGINE_DEVELOPMENT_MODE` | bool | `false` | Enable dev diagnostics (relaxed timeouts, verbose traces) |
| `real_network_enabled` | `SEARCH_ENGINE_REAL_NETWORK_ENABLED` | bool | `false` | Enable live outbound HTTP requests (requires preflight) |
| `allow_loopback_for_tests`| `SEARCH_ENGINE_ALLOW_LOOPBACK_FOR_TESTS` | bool | `false` | Allow loopback connections for test fixture servers only |
| `offline_mode` | `SEARCH_ENGINE_OFFLINE_MODE` | bool | `false` | Restrict engine to stored documents and index (no network) |
| `log_level` | `SEARCH_ENGINE_LOG_LEVEL` | string | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |
| `log_raw_queries` | `SEARCH_ENGINE_LOG_RAW_QUERIES` | bool | `false` | Log unredacted query text (deprecated; prefer `log_query_text`) |

### 3.8. News Ingestion & Clustering
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `news_max_items` | `SEARCH_ENGINE_NEWS_MAX_ITEMS` | int | `500` | Maximum news items stored in database |
| `news_retention_days` | `SEARCH_ENGINE_NEWS_RETENTION_DAYS` | int | `14` | Maximum age of news items before retention purge |
| `news_poll_interval_minutes`| `SEARCH_ENGINE_NEWS_POLL_INTERVAL_MINUTES` | int | `30` | Default feed polling interval (min: 15) |
| `news_summary_max_chars`| `SEARCH_ENGINE_NEWS_SUMMARY_MAX_CHARS` | int | `300` | Maximum characters preserved from feed summary |
| `news_images_enabled` | `SEARCH_ENGINE_NEWS_IMAGES_ENABLED` | bool | `false` | Ingest and thumbnail news enclosure images (Release B) |
| `news_stale_hours` | `SEARCH_ENGINE_NEWS_STALE_HOURS` | int | `24` | Hours without feed update before marking source unhealthy |

### 3.9. Search Experience & Panels
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `synopsis_min_chars` | `SEARCH_ENGINE_SYNOPSIS_MIN_CHARS` | int | `40` | Minimum characters for a verbatim synopsis sentence |
| `synopsis_max_chars` | `SEARCH_ENGINE_SYNOPSIS_MAX_CHARS` | int | `300` | Maximum characters for a verbatim synopsis sentence |
| `synopsis_min_coverage` | `SEARCH_ENGINE_SYNOPSIS_MIN_COVERAGE` | float | `0.6` | Minimum query term coverage required for synopsis |
| `synopsis_min_doc_score`| `SEARCH_ENGINE_SYNOPSIS_MIN_DOC_SCORE` | float | `0.0` | Minimum BM25 doc score to consider for synopsis |
| `preview_lead_chars` | `SEARCH_ENGINE_PREVIEW_LEAD_CHARS` | int | `200` | Maximum lead text length for PreviewCard |
| `recent_news_days` | `SEARCH_ENGINE_RECENT_NEWS_DAYS` | int | `7` | Maximum age of news items matched to search query |
| `recent_news_max` | `SEARCH_ENGINE_RECENT_NEWS_MAX` | int | `3` | Maximum news items returned in search recent-news block |

### 3.10. Release B Feature Flags
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `feature_images` | `SEARCH_ENGINE_FEATURE_IMAGES` | bool | `false` | Enable image extraction and thumbnail pipeline |
| `feature_entities` | `SEARCH_ENGINE_FEATURE_ENTITIES` | bool | `false` | Enable entity sidebar and Wikidata/Wikipedia cards |
| `feature_autocomplete` | `SEARCH_ENGINE_FEATURE_AUTOCOMPLETE`| bool | `false` | Enable prefix search suggestions |
| `feature_remote_test` | `SEARCH_ENGINE_FEATURE_REMOTE_TEST` | bool | `false` | Enable remote access testing profile |

### 3.11. HTTP Server & Security
| Setting | Env Var | Type | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `server_host` | `SEARCH_ENGINE_SERVER_HOST` | string | `127.0.0.1` | Network interface to bind HTTP API server |
| `server_port` | `SEARCH_ENGINE_SERVER_PORT` | int | `8000` | TCP port to listen on |
| `api_auth_required` | `SEARCH_ENGINE_API_AUTH_REQUIRED` | bool | `false` | Require `Authorization: Bearer <key>` header |
| `api_allowed_hosts` | `SEARCH_ENGINE_API_ALLOWED_HOSTS` | tuple | `127.0.0.1,...` | Host header allowlist (prevents DNS rebinding) |
| `api_cors_enabled` | `SEARCH_ENGINE_API_CORS_ENABLED` | bool | `false` | Enable Cross-Origin Resource Sharing headers |
| `api_cors_origins` | `SEARCH_ENGINE_API_CORS_ORIGINS` | tuple | `()` | Allowed CORS origins (must be explicit origins) |
| `api_rate_limit_rpm` | `SEARCH_ENGINE_API_RATE_LIMIT_RPM` | int | `60` | Client rate limit in requests per minute |
| `api_request_timeout_seconds`| `SEARCH_ENGINE_API_REQUEST_TIMEOUT_SECONDS` | float | `5.0` | Maximum API request processing timeout |
| `log_query_text` | `SEARCH_ENGINE_LOG_QUERY_TEXT` | bool | `false` | Include raw search query text in logs (D16: default false) |
| `api_key_hash` | `SEARCH_ENGINE_API_KEY_HASH` | SecretStr | `None` | Pre-hashed operator API key for non-loopback authentication |
