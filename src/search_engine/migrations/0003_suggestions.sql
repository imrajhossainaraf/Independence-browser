-- 0003_suggestions.sql: Prefix-optimized autocomplete suggestions table (Step 21, D19, D20)

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    normalized_prefix TEXT NOT NULL,
    source_type TEXT NOT NULL, -- 'entity', 'headline', 'title', 'term'
    priority INTEGER NOT NULL, -- 1=entity, 2=headline, 3=title, 4=term
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(text, normalized_prefix)
);

CREATE INDEX IF NOT EXISTS idx_suggestions_prefix ON suggestions (normalized_prefix, priority, text);
