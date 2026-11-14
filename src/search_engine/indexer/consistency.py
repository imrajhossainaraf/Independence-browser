"""Verification and consistency validation for FTS5 index tables."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConsistencyReport:
    is_consistent: bool
    total_active_docs: int
    doc_fts_count: int
    doc_fts_tri_count: int
    missing_in_fts: list[int] = field(default_factory=list)
    orphans_in_fts: list[int] = field(default_factory=list)
    missing_in_tri: list[int] = field(default_factory=list)
    orphans_in_tri: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_consistent": self.is_consistent,
            "total_active_docs": self.total_active_docs,
            "doc_fts_count": self.doc_fts_count,
            "doc_fts_tri_count": self.doc_fts_tri_count,
            "missing_in_fts": list(self.missing_in_fts),
            "orphans_in_fts": list(self.orphans_in_fts),
            "missing_in_tri": list(self.missing_in_tri),
            "orphans_in_tri": list(self.orphans_in_tri),
            "errors": list(self.errors),
        }

    def render(self) -> str:
        status = "PASSED (Consistent)" if self.is_consistent else "FAILED (Inconsistent)"
        lines = [
            f"Index Consistency Check: {status}",
            f"Active documents: {self.total_active_docs}",
            f"doc_fts rows:     {self.doc_fts_count}",
            f"doc_fts_tri rows: {self.doc_fts_tri_count}",
        ]
        if self.missing_in_fts:
            lines.append(
                f"Missing in doc_fts ({len(self.missing_in_fts)}): {self.missing_in_fts[:10]}"
            )
        if self.orphans_in_fts:
            lines.append(
                f"Orphans in doc_fts ({len(self.orphans_in_fts)}): {self.orphans_in_fts[:10]}"
            )
        if self.missing_in_tri:
            lines.append(
                f"Missing in doc_fts_tri ({len(self.missing_in_tri)}): {self.missing_in_tri[:10]}"
            )
        if self.orphans_in_tri:
            lines.append(
                f"Orphans in doc_fts_tri ({len(self.orphans_in_tri)}): {self.orphans_in_tri[:10]}"
            )
        if self.errors:
            lines.append("Errors encountered:")
            for err in self.errors:
                lines.append(f"  - {err}")
        return "\n".join(lines)


def verify_index_consistency(conn: sqlite3.Connection) -> ConsistencyReport:
    """Verify that every active document has exactly one row in doc_fts and doc_fts_tri.

    Also checks that no orphan rows exist in either virtual table.
    """
    errors: list[str] = []

    # 1. Fetch active document IDs
    cur = conn.execute(
        """
        SELECT id FROM documents
        WHERE status = 'active' AND doc_type IN ('page', 'pdf', 'text', 'news')
        """
    )
    active_ids = {int(r[0]) for r in cur.fetchall()}

    # 2. Fetch doc_fts rowids
    try:
        cur = conn.execute("SELECT rowid FROM doc_fts")
        fts_ids = {int(r[0]) for r in cur.fetchall()}
    except sqlite3.Error as e:
        errors.append(f"Failed querying doc_fts: {e}")
        fts_ids = set()

    # 3. Fetch doc_fts_tri rowids
    try:
        cur = conn.execute("SELECT rowid FROM doc_fts_tri")
        tri_ids = {int(r[0]) for r in cur.fetchall()}
    except sqlite3.Error as e:
        errors.append(f"Failed querying doc_fts_tri: {e}")
        tri_ids = set()

    # 4. Run FTS5 internal integrity checks (if connection is not query-only / read-only)
    is_query_only = False
    try:
        row = conn.execute("PRAGMA query_only").fetchone()
        if row and row[0]:
            is_query_only = True
    except sqlite3.Error:
        pass

    if not is_query_only:
        try:
            conn.execute("INSERT INTO doc_fts(doc_fts) VALUES('integrity-check')")
        except sqlite3.Error as e:
            if "readonly" not in str(e).lower():
                errors.append(f"doc_fts integrity-check failed: {e}")

        try:
            conn.execute("INSERT INTO doc_fts_tri(doc_fts_tri) VALUES('integrity-check')")
        except sqlite3.Error as e:
            if "readonly" not in str(e).lower():
                errors.append(f"doc_fts_tri integrity-check failed: {e}")

    # 5. Compute differences
    missing_in_fts = sorted(active_ids - fts_ids)
    orphans_in_fts = sorted(fts_ids - active_ids)
    missing_in_tri = sorted(active_ids - tri_ids)
    orphans_in_tri = sorted(tri_ids - active_ids)

    is_consistent = (
        len(missing_in_fts) == 0
        and len(orphans_in_fts) == 0
        and len(missing_in_tri) == 0
        and len(orphans_in_tri) == 0
        and len(errors) == 0
    )

    return ConsistencyReport(
        is_consistent=is_consistent,
        total_active_docs=len(active_ids),
        doc_fts_count=len(fts_ids),
        doc_fts_tri_count=len(tri_ids),
        missing_in_fts=missing_in_fts,
        orphans_in_fts=orphans_in_fts,
        missing_in_tri=missing_in_tri,
        orphans_in_tri=orphans_in_tri,
        errors=errors,
    )
