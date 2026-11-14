-- 0005_operator_blocks.sql: persistent operator blocks for hosts, domains, and URL prefixes.

CREATE TABLE IF NOT EXISTS operator_blocks (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('host', 'domain', 'prefix')),
    target TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL DEFAULT 'operator_block',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_operator_blocks_target ON operator_blocks (target);
