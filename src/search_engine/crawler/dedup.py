"""Deduplication, canonical grouping, and redirect consolidation (Step 17, decision D7, D12).

Implements:
- Exact duplicate detection by content hash.
- Canonical grouping (rel=canonical honored ONLY for the same registered domain and safe targets).
- Grouping of trailing-slash and index-file variants (/index.html, /index.htm, / -> /).
- Redirect consolidation: deactivates superseded documents when a URL starts redirecting.
"""

from __future__ import annotations

import sqlite3
import urllib.parse

from search_engine.crawler.normalize import normalize
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.indexer.build import deactivate_document

_INDEX_SUFFIXES = (
    "/index.html",
    "/index.htm",
    "/index.php",
    "/index.asp",
    "/default.html",
    "/default.htm",
)


def canonical_group_key(
    url: str,
    content_hash: str | None = None,
    *,
    allow_loopback: bool = False,
) -> str:
    """Derive canonical grouping key for a URL or exact content hash.

    Normalizes protocol, trailing slashes, and index file variants (/index.html -> /).
    """
    if content_hash and content_hash.strip():
        return f"hash:{content_hash.strip().lower()}"

    raw = url.strip()
    if not raw:
        return ""

    try:
        norm = normalize(raw, allow_loopback=allow_loopback)
        clean_url = norm.key
    except (UrlRejected, ValueError):
        clean_url = raw

    parsed = urllib.parse.urlsplit(clean_url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"

    # Strip default port if present in netloc
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    # Normalize index file variants to directory root
    path_lower = path.lower()
    for suffix in _INDEX_SUFFIXES:
        if path_lower.endswith(suffix):
            path = path[: -len(suffix)] or "/"
            break

    # Strip trailing slash if not root
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query_str = f"?{parsed.query}" if parsed.query else ""
    return f"url:{netloc}{path}{query_str}"


def is_valid_canonical(
    canonical_url: str,
    final_url: str,
    *,
    allow_loopback: bool = False,
) -> bool:
    """Validate that rel=canonical is a safe target and shares the same registered domain."""
    if not canonical_url or not final_url:
        return False

    try:
        norm_canonical = normalize(canonical_url, allow_loopback=allow_loopback)
        norm_final = normalize(final_url, allow_loopback=allow_loopback)
    except (UrlRejected, ValueError):
        return False

    # rel=canonical is honored ONLY for the same registered domain
    if norm_canonical.registered_domain != norm_final.registered_domain:
        return False

    return True


def resolve_canonical_group(
    conn: sqlite3.Connection,
    *,
    final_url: str,
    canonical_url: str | None = None,
    content_hash: str | None = None,
    allow_loopback: bool = False,
) -> int | None:
    """Find matching canonical_group_id from existing documents or return None for new group leader.

    Checks:
    1. Exact duplicates by content_hash (any host/domain).
    2. Canonical URL match (verified same registered domain).
    3. Trailing-slash and index-file variants on the same host/domain.
    """
    # 1. Exact duplicate by content hash
    if content_hash and content_hash.strip():
        row = conn.execute(
            """
            SELECT id, canonical_group_id FROM documents
            WHERE content_hash = ? AND status != 'removed'
            LIMIT 1
            """,
            (content_hash.strip(),),
        ).fetchone()
        if row is not None:
            return int(row[1] or row[0])

    # 2. rel=canonical match (same registered domain)
    if canonical_url and is_valid_canonical(
        canonical_url, final_url, allow_loopback=allow_loopback
    ):
        row = conn.execute(
            """
            SELECT id, canonical_group_id FROM documents
            WHERE (canonical_url = ? OR final_url = ?) AND status != 'removed'
            LIMIT 1
            """,
            (canonical_url, canonical_url),
        ).fetchone()
        if row is not None:
            return int(row[1] or row[0])

    # 3. Trailing-slash and index-file variants on the same host/domain
    target_key = canonical_group_key(
        canonical_url
        if (
            canonical_url
            and is_valid_canonical(canonical_url, final_url, allow_loopback=allow_loopback)
        )
        else final_url,
        allow_loopback=allow_loopback,
    )

    cur = conn.execute(
        """
        SELECT id, final_url, canonical_url, canonical_group_id
        FROM documents
        WHERE status != 'removed'
        """
    )
    for r in cur.fetchall():
        doc_id = int(r[0])
        group_id = int(r[3] or doc_id)
        other_final = str(r[1] or "")
        other_canonical = str(r[2] or "") if r[2] else None

        # Compare candidate keys
        cand_key = canonical_group_key(
            other_canonical
            if (
                other_canonical
                and is_valid_canonical(other_canonical, other_final, allow_loopback=allow_loopback)
            )
            else other_final,
            allow_loopback=allow_loopback,
        )
        if cand_key == target_key:
            return group_id

    return None


def consolidate_redirect(
    conn: sqlite3.Connection,
    *,
    from_url_id: int,
    to_url_id: int | None = None,
    target_url: str = "",
) -> bool:
    """Consolidate redirect: deactivates any existing document on the hopped URL."""
    row = conn.execute("SELECT id FROM documents WHERE url_id = ?", (from_url_id,)).fetchone()
    if row is not None:
        doc_id = int(row[0])
        deactivate_document(conn, doc_id, reason="redirected")
        return True
    return False
