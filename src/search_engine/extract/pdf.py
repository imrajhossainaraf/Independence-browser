"""PDF content adapter executing in an isolated subprocess."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import PurePosixPath

from search_engine.extract.models import (
    AdapterResult,
    AdapterStatus,
    ExtractedDocument,
)
from search_engine.extract.text import _detect_language
from search_engine.extract.worker import run_isolated_process

_WORDS = re.compile(r"\b\w+\b")
_MAX_PDF_BYTES = 5 * 1024 * 1024  # 5 MiB per D10
_MAX_PDF_PAGES = 50  # 50 pages per D10
_DEFAULT_TIMEOUT = 10.0  # 10 seconds per D10
_DEFAULT_MEMORY_LIMIT = 512 * 1024 * 1024  # 512 MiB per D10


class PdfAdapter:
    """Extracts text and metadata from PDF files using an isolated subprocess."""

    def can_handle(self, content_type: str, url: str) -> bool:
        lower_type = (content_type or "").lower().split(";")[0].strip()
        if lower_type == "application/pdf":
            return True
        path = urllib.parse.urlsplit(url).path.lower()
        return path.endswith(".pdf")

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
        max_bytes: int = _MAX_PDF_BYTES,
        max_pages: int = _MAX_PDF_PAGES,
        timeout: float = _DEFAULT_TIMEOUT,
        memory_limit_bytes: int = _DEFAULT_MEMORY_LIMIT,
    ) -> AdapterResult:
        warnings: list[str] = []

        # 1. Size check (D10: 5 MiB cap)
        if len(content) > max_bytes:
            warnings.append("pdf_size_limit_exceeded")
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=warnings,
            )
            return AdapterResult(
                doc_type="pdf",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings,
            )

        # 2. Write to temp file for worker
        temp_fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(temp_fd, "wb") as f:
                f.write(content)

            cmd = [
                sys.executable,
                "-m",
                "search_engine.extract.pdf_worker",
                temp_path,
                str(max_pages),
                str(500 * 1024),
            ]

            proc_res = run_isolated_process(
                cmd,
                timeout_seconds=timeout,
                memory_limit_bytes=memory_limit_bytes,
            )
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

        # 3. Handle worker outcomes
        if proc_res.memory_limit_unavailable:
            warnings.append("pdf_memory_limit_unavailable")

        if proc_res.killed_by_timeout:
            warnings.append("pdf_timeout")
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=warnings,
            )
            return AdapterResult(
                doc_type="pdf",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings,
            )

        if proc_res.killed_by_memory:
            warnings.append("pdf_memory_limit_exceeded")
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=warnings,
            )
            return AdapterResult(
                doc_type="pdf",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings,
            )

        # 4. Parse worker JSON output
        worker_data = {}
        try:
            worker_data = json.loads(proc_res.stdout.strip())
        except Exception:  # noqa: BLE001
            warnings.append("pdf_worker_parse_error")
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=warnings,
            )
            return AdapterResult(
                doc_type="pdf",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings,
            )

        worker_warnings = worker_data.get("warnings", [])
        warnings.extend(worker_warnings)

        worker_status = worker_data.get("status", "error")
        title = worker_data.get("title", "")
        if not title:
            path_name = PurePosixPath(urllib.parse.urlsplit(final_url).path).name
            title = path_name or "PDF Document"

        text = worker_data.get("text", "")
        published_at = worker_data.get("created_at")

        if worker_status in ("encrypted", "scanned", "corrupt", "error") or not text.strip():
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title=title,
                text="",
                published_at=published_at,
                warnings=warnings,
            )
            return AdapterResult(
                doc_type="pdf",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings,
            )

        # Valid active extracted PDF
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        word_count = len(_WORDS.findall(text))
        lang = _detect_language(text)

        doc = ExtractedDocument(
            final_url=final_url,
            canonical_url=None,
            title=title,
            text=text,
            lang=lang,
            published_at=published_at,
            content_hash=content_hash,
            word_count=word_count,
            warnings=warnings,
        )
        return AdapterResult(
            doc_type="pdf",
            status=AdapterStatus.ACTIVE,
            document=doc,
            warnings=warnings,
        )
