"""Plain text content adapter."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from pathlib import PurePosixPath

import charset_normalizer

from search_engine.extract.models import (
    AdapterResult,
    AdapterStatus,
    ExtractedDocument,
)

_WORDS = re.compile(r"\b\w+\b")
_MAX_BYTES_DEFAULT = 2 * 1024 * 1024  # 2 MiB

# Unicode script ranges for language heuristic (D10)
_SCRIPT_PATTERNS = [
    (re.compile(r"[\u0400-\u04FF]"), "ru"),  # Cyrillic
    (re.compile(r"[\u3040-\u309F\u30A0-\u30FF]"), "ja"),  # Japanese Kana
    (re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF]"), "ko"),  # Korean Hangul
    (re.compile(r"[\u4E00-\u9FFF]"), "zh"),  # CJK Ideographs
    (re.compile(r"[\u0600-\u06FF]"), "ar"),  # Arabic
    (re.compile(r"[\u0590-\u05FF]"), "he"),  # Hebrew
    (re.compile(r"[\u0900-\u097F]"), "hi"),  # Devanagari (Hindi)
    (re.compile(r"[\u0370-\u03FF]"), "el"),  # Greek
    (re.compile(r"[\u0E00-\u0E7F]"), "th"),  # Thai
]


def _detect_language(text: str) -> str:
    """Detect language for plain text via script heuristic or und."""
    sample = text[:4000]
    total_letters = sum(1 for ch in sample if ch.isalpha())
    if total_letters > 20:
        for pattern, code in _SCRIPT_PATTERNS:
            matches = len(pattern.findall(sample))
            if matches >= 10 and (matches / total_letters) > 0.20:
                return code
    return "und"


def _decode_text(content: bytes, max_bytes: int) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if len(content) > max_bytes:
        content = content[:max_bytes]
        warnings.append("text_truncated_bytes")

    try:
        return content.decode("utf-8"), warnings
    except UnicodeDecodeError:
        pass

    best = charset_normalizer.from_bytes(content).best()
    if best is not None and best.encoding:
        try:
            return str(best), warnings
        except (ValueError, LookupError, UnicodeDecodeError):
            pass

    return content.decode("utf-8", errors="replace"), warnings


class PlainTextAdapter:
    """Adapter for plain text files."""

    def can_handle(self, content_type: str, url: str) -> bool:
        lower_type = (content_type or "").lower().split(";")[0].strip()
        if lower_type == "text/plain":
            return True
        path = urllib.parse.urlsplit(url).path.lower()
        return path.endswith(".txt")

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
        max_bytes: int = _MAX_BYTES_DEFAULT,
    ) -> AdapterResult:
        try:
            text_str, warnings = _decode_text(content, max_bytes)
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(
                doc_type="text",
                status=AdapterStatus.UNSUPPORTED,
                warnings=[f"decode_error: {exc}"],
            )

        trimmed = text_str.strip()
        if not trimmed:
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=warnings + ["empty_text_content"],
            )
            return AdapterResult(
                doc_type="text",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings + ["empty_text_content"],
            )

        # Title: first non-empty line or file basename
        first_line = trimmed.splitlines()[0].strip()
        if first_line and len(first_line) <= 120:
            title = first_line
        else:
            path_name = PurePosixPath(urllib.parse.urlsplit(final_url).path).name
            title = path_name or "Plain Text Document"

        lang = _detect_language(trimmed)
        content_hash = hashlib.sha256(trimmed.encode("utf-8")).hexdigest()
        word_count = len(_WORDS.findall(trimmed))

        doc = ExtractedDocument(
            final_url=final_url,
            canonical_url=None,
            title=title,
            text=trimmed,
            lang=lang,
            content_hash=content_hash,
            word_count=word_count,
            warnings=warnings,
        )
        return AdapterResult(
            doc_type="text",
            status=AdapterStatus.ACTIVE,
            document=doc,
            warnings=warnings,
        )
