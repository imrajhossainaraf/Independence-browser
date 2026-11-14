"""Headline clustering and Top Stories scoring per D16."""

from __future__ import annotations

import math
import re
import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from dateutil import parser as date_parser

_WORD_PATTERN = re.compile(r"\b\w+\b")


def title_shingles(title: str, k: int = 2) -> set[str]:
    """Extract normalized word k-shingles from a title string.

    Tokens are lowercased and stripped of punctuation.
    If the title has fewer than k words, returns unigram words.
    """
    words = [w.lower() for w in _WORD_PATTERN.findall(title) if w]
    if not words:
        return set()
    if len(words) < k:
        return set(words)
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard_similarity(s1: set[str], s2: set[str]) -> float:
    """Compute the Jaccard similarity between two sets of shingles.

    Returns float in range [0.0, 1.0].
    """
    if not s1 or not s2:
        return 0.0
    intersection = len(s1 & s2)
    union = len(s1 | s2)
    if union == 0:
        return 0.0
    return intersection / union


def compute_top_story_score(distinct_sources: int, age_in_hours: float) -> float:
    """Compute Top stories score per D16.

    Formula: distinct_sources * exp(-age_in_hours / 12)
    """
    safe_age = max(0.0, age_in_hours)
    return float(distinct_sources * math.exp(-safe_age / 12.0))


def _parse_timestamp(iso_str: str) -> datetime:
    """Parse ISO timestamp into UTC datetime."""
    try:
        dt = date_parser.parse(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        res: datetime = dt.astimezone(UTC)
        return res
    except (ValueError, OverflowError):
        return datetime.now(UTC)


@dataclass
class NewsCluster:
    """A multi-source cluster of news items representing a single story."""

    id: int | None
    headline_item_id: str
    item_ids: list[str] = field(default_factory=list)
    distinct_sources: int = 0
    score: float = 0.0
    created_at: str = ""
    updated_at: str = ""


def cluster_news_items(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    min_jaccard: float = 0.6,
    window_hours: float = 48.0,
) -> list[NewsCluster]:
    """Group active news items within window_hours into clusters per D16.

    Clustering criteria:
    - Items must come from different sources to connect.
    - Title shingles Jaccard similarity must be >= min_jaccard (default 0.6).
    - Published timestamps must be within window_hours (default 48 hours).
    - Clusters must contain at least 2 distinct sources.
    - Headline item is the earliest item from the highest-quality source.
      Source quality order: lowest consecutive_failures, then lowest source_id.
    """
    if now is None:
        now = datetime.now(UTC)
    now_iso = now.isoformat()

    # 1. Fetch source quality rankings
    source_rows = conn.execute(
        "SELECT id, consecutive_failures FROM news_sources ORDER BY id ASC"
    ).fetchall()
    source_quality: dict[int, tuple[int, int]] = {
        int(r["id"]): (int(r["consecutive_failures"]), int(r["id"])) for r in source_rows
    }

    # 2. Fetch active news items
    # We examine all active items; window_hours check is performed pairwise
    items_query = """
        SELECT id, source_id, cluster_id, title, published_at
        FROM news_items
        WHERE status = 'active'
        ORDER BY published_at DESC
    """
    rows = conn.execute(items_query).fetchall()
    if not rows:
        return []

    items: list[dict[str, Any]] = []
    shingles_list: list[set[str]] = []
    times_list: list[datetime] = []

    for r in rows:
        item_id = str(r["id"])
        source_id = int(r["source_id"])
        cluster_id = int(r["cluster_id"]) if r["cluster_id"] is not None else None
        title = str(r["title"])
        pub_str = str(r["published_at"])
        dt = _parse_timestamp(pub_str)

        items.append(
            {
                "id": item_id,
                "source_id": source_id,
                "cluster_id": cluster_id,
                "title": title,
                "published_at": pub_str,
                "dt": dt,
            }
        )
        shingles_list.append(title_shingles(title))
        times_list.append(dt)

    n = len(items)
    adj: dict[int, list[int]] = defaultdict(list)

    # 3. Build graph: edges between different sources within window with Jaccard >= min_jaccard
    window_seconds = window_hours * 3600.0
    for i in range(n):
        for j in range(i + 1, n):
            # Only connect items from different sources
            if items[i]["source_id"] == items[j]["source_id"]:
                continue
            # Check 48-hour window
            time_diff = abs((times_list[i] - times_list[j]).total_seconds())
            if time_diff > window_seconds:
                continue
            # Check Jaccard similarity
            sim = jaccard_similarity(shingles_list[i], shingles_list[j])
            if sim >= min_jaccard:
                adj[i].append(j)
                adj[j].append(i)

    # 4. Find connected components using BFS
    visited = [False] * n
    clustered_indices: set[int] = set()
    clusters: list[NewsCluster] = []

    for i in range(n):
        if visited[i]:
            continue

        component: list[int] = []
        queue = deque([i])
        visited[i] = True

        while queue:
            curr = queue.popleft()
            component.append(curr)
            for neighbor in adj[curr]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    queue.append(neighbor)

        # A cluster must span at least 2 distinct sources
        distinct_sources = len({items[idx]["source_id"] for idx in component})
        if len(component) >= 2 and distinct_sources >= 2:
            clustered_indices.update(component)

            # Choose headline item: earliest item from highest quality source
            def headline_key(idx: int) -> tuple[tuple[int, int], datetime, str]:
                s_id = items[idx]["source_id"]
                quality = source_quality.get(s_id, (999, s_id))
                return (quality, items[idx]["dt"], items[idx]["id"])

            best_idx = min(component, key=headline_key)
            headline_item_id = items[best_idx]["id"]
            headline_dt = items[best_idx]["dt"]

            # Compute score
            age_hours = max(0.0, (now - headline_dt).total_seconds() / 3600.0)
            score = compute_top_story_score(distinct_sources, age_hours)

            comp_item_ids = [items[idx]["id"] for idx in component]

            # Reuse existing cluster id if any item already has one
            existing_cluster_ids = {
                items[idx]["cluster_id"]
                for idx in component
                if items[idx]["cluster_id"] is not None
            }
            chosen_cluster_id: int | None = None
            if existing_cluster_ids:
                chosen_cluster_id = min(existing_cluster_ids)

            clusters.append(
                NewsCluster(
                    id=chosen_cluster_id,
                    headline_item_id=headline_item_id,
                    item_ids=comp_item_ids,
                    distinct_sources=distinct_sources,
                    score=score,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )

    # 5. Persist clusters in database
    active_cluster_ids: set[int] = set()
    for cl in clusters:
        if cl.id is not None:
            # Verify existing cluster exists in db
            exists = conn.execute("SELECT id FROM news_clusters WHERE id = ?", (cl.id,)).fetchone()
            if exists:
                conn.execute(
                    "UPDATE news_clusters SET headline_item_id = ?, updated_at = ? WHERE id = ?",
                    (cl.headline_item_id, now_iso, cl.id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO news_clusters (headline_item_id, created_at, updated_at) "
                    "VALUES (?, ?, ?)",
                    (cl.headline_item_id, now_iso, now_iso),
                )
                cl.id = int(cur.lastrowid or 0)
        else:
            cur = conn.execute(
                "INSERT INTO news_clusters (headline_item_id, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (cl.headline_item_id, now_iso, now_iso),
            )
            cl.id = int(cur.lastrowid or 0)

        active_cluster_ids.add(cl.id)

        # Update items with this cluster_id
        for item_id in cl.item_ids:
            conn.execute(
                "UPDATE news_items SET cluster_id = ? WHERE id = ?",
                (cl.id, item_id),
            )

    # 6. Unset cluster_id for items not in any active cluster
    unclustered_ids = [items[idx]["id"] for idx in range(n) if idx not in clustered_indices]
    if unclustered_ids:
        conn.executemany(
            "UPDATE news_items SET cluster_id = NULL WHERE id = ?",
            [(i_id,) for i_id in unclustered_ids],
        )

    # 7. Remove any obsolete clusters from news_clusters
    if active_cluster_ids:
        placeholders = ", ".join(["?"] * len(active_cluster_ids))
        conn.execute(
            f"DELETE FROM news_clusters WHERE id NOT IN ({placeholders})",  # noqa: S608
            tuple(active_cluster_ids),
        )
    else:
        conn.execute("DELETE FROM news_clusters")

    # Sort clusters by score descending
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters
