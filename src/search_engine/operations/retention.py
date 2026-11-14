"""Data retention enforcement and storage pruning (Decision D16 & Step 25)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from search_engine.db import Database
from search_engine.storage import FileStore, recover


@dataclass(frozen=True, slots=True)
class RetentionReport:
    news_items_expired: int
    news_items_pruned_for_cap: int
    documents_deindexed: int
    temp_files_removed: int
    orphan_raw_count: int
    orphan_images_count: int
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "news_items_expired": self.news_items_expired,
            "news_items_pruned_for_cap": self.news_items_pruned_for_cap,
            "documents_deindexed": self.documents_deindexed,
            "temp_files_removed": self.temp_files_removed,
            "orphan_raw_count": self.orphan_raw_count,
            "orphan_images_count": self.orphan_images_count,
            "details": list(self.details),
        }


def apply_retention_purge(
    db: Database,
    store: FileStore | None = None,
    *,
    news_retention_days: int = 14,
    news_max_items: int = 500,
    purge_orphaned_files: bool = True,
    now: datetime | None = None,
) -> RetentionReport:
    """Enforce data retention.

    Deactivates stale news items, caps at news_max_items, and cleans orphaned files.
    """
    if now is None:
        now = datetime.now(UTC)
    now_iso = now.isoformat()
    cutoff_iso = (now - timedelta(days=news_retention_days)).isoformat()

    expired_count = 0
    cap_pruned_count = 0
    deindexed_docs = 0
    temp_removed = 0
    orphan_raw = 0
    orphan_images = 0
    details: list[str] = []

    with db.transaction() as conn:
        # 1. Deactivate news items older than news_retention_days
        cur_exp = conn.execute(
            """
            SELECT id FROM news_items
            WHERE status = 'active' AND published_at < ?
            """,
            (cutoff_iso,),
        )
        expired_ids = [str(r[0]) for r in cur_exp.fetchall()]

        for item_id in expired_ids:
            conn.execute(
                "UPDATE news_items SET status = 'inactive', updated_at = ? WHERE id = ?",
                (now_iso, item_id),
            )
            expired_count += 1

            # Deindex corresponding document
            doc_row = conn.execute(
                "SELECT id FROM documents WHERE news_item_id = ?", (item_id,)
            ).fetchone()
            if doc_row:
                d_id = int(doc_row[0])
                conn.execute(
                    "UPDATE documents SET status = 'inactive', updated_at = ? WHERE id = ?",
                    (now_iso, d_id),
                )
                cur_fts1 = conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d_id,))
                cur_fts2 = conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (d_id,))
                deindexed_docs += (cur_fts1.rowcount + cur_fts2.rowcount) // 2

        if expired_count > 0:
            details.append(
                f"deactivated {expired_count} news items older than {news_retention_days} days"
            )

        # 2. Enforce active news items cap (prune oldest first)
        cur_active = conn.execute(
            """
            SELECT id FROM news_items
            WHERE status = 'active'
            ORDER BY published_at ASC
            """
        )
        active_ids = [str(r[0]) for r in cur_active.fetchall()]

        if len(active_ids) > news_max_items:
            surplus = len(active_ids) - news_max_items
            prune_ids = active_ids[:surplus]

            for item_id in prune_ids:
                conn.execute(
                    "UPDATE news_items SET status = 'inactive', updated_at = ? WHERE id = ?",
                    (now_iso, item_id),
                )
                cap_pruned_count += 1

                doc_row = conn.execute(
                    "SELECT id FROM documents WHERE news_item_id = ?", (item_id,)
                ).fetchone()
                if doc_row:
                    d_id = int(doc_row[0])
                    conn.execute(
                        "UPDATE documents SET status = 'inactive', updated_at = ? WHERE id = ?",
                        (now_iso, d_id),
                    )
                    cur_fts1 = conn.execute("DELETE FROM doc_fts WHERE rowid = ?", (d_id,))
                    cur_fts2 = conn.execute("DELETE FROM doc_fts_tri WHERE rowid = ?", (d_id,))
                    deindexed_docs += (cur_fts1.rowcount + cur_fts2.rowcount) // 2

            details.append(
                f"pruned {cap_pruned_count} excess news items to meet cap of {news_max_items}"
            )

        # 3. Clean up storage files if store provided
        if store is not None and purge_orphaned_files:
            rec_report = recover(conn, store, remove_temp=True)
            temp_removed = rec_report.temp_removed
            orphan_raw = len(rec_report.orphan_raw)
            orphan_images = len(rec_report.orphan_images)
            if temp_removed > 0:
                details.append(f"removed {temp_removed} stale temporary files")
            if orphan_raw > 0 or orphan_images > 0:
                details.append(
                    f"detected {orphan_raw} orphan raw files and {orphan_images} orphan images"
                )

    return RetentionReport(
        news_items_expired=expired_count,
        news_items_pruned_for_cap=cap_pruned_count,
        documents_deindexed=deindexed_docs,
        temp_files_removed=temp_removed,
        orphan_raw_count=orphan_raw,
        orphan_images_count=orphan_images,
        details=details,
    )
