-- 0001_initial.sql: every table the engine needs (decision D8). Times are ISO-8601 UTC text.

CREATE TABLE hosts (
    id INTEGER PRIMARY KEY,
    host TEXT NOT NULL UNIQUE,
    registered_domain TEXT NOT NULL,
    crawl_delay_seconds REAL,
    next_allowed_at TEXT,
    pages_stored INTEGER NOT NULL DEFAULT 0 CHECK (pages_stored >= 0),
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    created_at TEXT NOT NULL
);
CREATE INDEX idx_hosts_domain ON hosts (registered_domain);

CREATE TABLE robots_cache (
    origin TEXT PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('parsed', 'allow_all', 'disallow_all')),
    body TEXT,
    crawl_delay_seconds REAL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

-- URLs and the crawl frontier.
CREATE TABLE urls (
    id INTEGER PRIMARY KEY,
    url_key TEXT NOT NULL UNIQUE,
    host_id INTEGER NOT NULL REFERENCES hosts (id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'leased', 'done', 'retry', 'failed_permanent', 'blocked', 'skipped')),
    depth INTEGER NOT NULL DEFAULT 0 CHECK (depth >= 0),
    priority INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL CHECK (source IN ('seed', 'sitemap', 'feed', 'link', 'redirect')),
    parent_url_id INTEGER REFERENCES urls (id),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    lease_expires_at TEXT,
    next_attempt_at TEXT,
    last_reason TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_urls_frontier ON urls (status, priority, depth, id);
CREATE INDEX idx_urls_host ON urls (host_id, status);

CREATE TABLE fetch_attempts (
    id INTEGER PRIMARY KEY,
    url_id INTEGER NOT NULL REFERENCES urls (id) ON DELETE CASCADE,
    attempted_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT,
    status_code INTEGER,
    bytes INTEGER CHECK (bytes IS NULL OR bytes >= 0),
    duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    final_url TEXT
);
CREATE INDEX idx_attempts_url ON fetch_attempts (url_id, attempted_at);

CREATE TABLE links (
    id INTEGER PRIMARY KEY,
    from_url_id INTEGER NOT NULL REFERENCES urls (id) ON DELETE CASCADE,
    to_url_key TEXT NOT NULL,
    nofollow INTEGER NOT NULL DEFAULT 0 CHECK (nofollow IN (0, 1)),
    discovered_at TEXT NOT NULL,
    UNIQUE (from_url_id, to_url_key)
);

-- Images (Release B writes here; the table exists so the schema is stable).
CREATE TABLE images (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    page_url TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    width INTEGER CHECK (width IS NULL OR width > 0),
    height INTEGER CHECK (height IS NULL OR height > 0),
    bytes INTEGER CHECK (bytes IS NULL OR bytes >= 0),
    alt_text TEXT,
    license TEXT,
    attribution TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed'))
);

-- News.
CREATE TABLE news_sources (
    id INTEGER PRIMARY KEY,
    feed_url_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    poll_interval_minutes INTEGER NOT NULL CHECK (poll_interval_minutes >= 15),
    images_allowed INTEGER NOT NULL DEFAULT 0 CHECK (images_allowed IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    etag TEXT,
    last_modified_header TEXT,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    next_poll_at TEXT
);

CREATE TABLE news_clusters (
    id INTEGER PRIMARY KEY,
    headline_item_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE news_items (
    id TEXT PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES news_sources (id),
    cluster_id INTEGER REFERENCES news_clusters (id),
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    published_at TEXT NOT NULL,
    published_is_first_seen INTEGER NOT NULL DEFAULT 0 CHECK (published_is_first_seen IN (0, 1)),
    summary TEXT NOT NULL DEFAULT '' CHECK (length(summary) <= 300),
    category TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'und',
    image_id TEXT REFERENCES images (id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_news_items_recent ON news_items (status, published_at);
CREATE INDEX idx_news_items_cluster ON news_items (cluster_id);

-- Documents: crawled pages and news items share one table (news items have no urls row).
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    url_id INTEGER UNIQUE REFERENCES urls (id),
    news_item_id TEXT UNIQUE REFERENCES news_items (id),
    doc_type TEXT NOT NULL DEFAULT 'page' CHECK (doc_type IN ('page', 'pdf', 'text', 'news')),
    final_url TEXT,
    canonical_url TEXT,
    canonical_group_id INTEGER REFERENCES documents (id),
    content_type TEXT,
    lang TEXT NOT NULL DEFAULT 'und',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive', 'removed', 'noindex', 'metadata_only', 'unsupported')),
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    headings TEXT NOT NULL DEFAULT '',
    text_gz BLOB,
    word_count INTEGER NOT NULL DEFAULT 0 CHECK (word_count >= 0),
    content_hash TEXT,
    raw_hash TEXT,
    etag TEXT,
    last_modified_header TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    indexed_version INTEGER NOT NULL DEFAULT 0,
    warnings TEXT NOT NULL DEFAULT '',
    CHECK ((url_id IS NOT NULL) + (news_item_id IS NOT NULL) = 1)
);
CREATE INDEX idx_documents_status ON documents (status);
CREATE INDEX idx_documents_hash ON documents (content_hash);
CREATE INDEX idx_documents_group ON documents (canonical_group_id);
CREATE INDEX idx_documents_raw ON documents (raw_hash);

-- Index status (index version, last build) and operational history.
CREATE TABLE index_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    at TEXT NOT NULL,
    reason TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_events_run ON events (run_id, reason);

CREATE TABLE metrics (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    labels TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_metrics_name ON metrics (name, at);

-- Entities (Release B writes here; the table exists so the schema is stable).
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '' CHECK (length(summary) <= 400),
    image_id TEXT REFERENCES images (id),
    source_url TEXT NOT NULL UNIQUE,
    revision_id TEXT,
    license TEXT NOT NULL,
    attribution TEXT NOT NULL,
    retrieved_at TEXT NOT NULL
);

CREATE TABLE entity_aliases (
    entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    alias_norm TEXT NOT NULL,
    PRIMARY KEY (entity_id, alias_norm)
);
CREATE INDEX idx_entity_alias ON entity_aliases (alias_norm);

CREATE TABLE entity_facts (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 7),
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    UNIQUE (entity_id, position)
);
