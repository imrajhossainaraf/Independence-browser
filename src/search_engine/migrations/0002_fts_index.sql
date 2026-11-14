-- 0002_fts_index.sql: FTS5 inverted index and trigram tables (decision D4).

CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts USING fts5(
    title,
    headings,
    description,
    body,
    url_tokens,
    tokenize = 'porter unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts_tri USING fts5(
    title,
    body_prefix,
    tokenize = 'trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_fts_vocab USING fts5vocab(
    doc_fts,
    'row'
);

INSERT OR IGNORE INTO index_state (key, value) VALUES ('index_version', '1');
INSERT OR IGNORE INTO index_state (key, value) VALUES ('last_rebuild_at', '');
