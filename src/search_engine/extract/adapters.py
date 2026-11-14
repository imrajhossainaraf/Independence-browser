"""Unified content adapter interface, registry, and dispatcher."""

from __future__ import annotations

import urllib.parse
from typing import Protocol

from search_engine.extract.feed import FeedAdapter
from search_engine.extract.html import extract_html
from search_engine.extract.models import (
    AdapterResult,
    AdapterStatus,
    ExtractedDocument,
)
from search_engine.extract.pdf import PdfAdapter
from search_engine.extract.sitemap import SitemapAdapter
from search_engine.extract.text import PlainTextAdapter


class ContentAdapter(Protocol):
    """Protocol for content extraction adapters."""

    def can_handle(self, content_type: str, url: str) -> bool:
        """Return True if this adapter can extract content of this type/url."""
        ...

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
    ) -> AdapterResult:
        """Extract searchable document, metadata, or discovered items."""
        ...


class HtmlAdapter:
    """Adapter wrapping HTML extraction."""

    def can_handle(self, content_type: str, url: str) -> bool:
        lower_type = (content_type or "").lower().split(";")[0].strip()
        if lower_type in ("text/html", "application/xhtml+xml"):
            return True
        path = urllib.parse.urlsplit(url).path.lower()
        if path.endswith((".html", ".htm", ".xhtml", "/")) or not path:
            return True
        return False

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
    ) -> AdapterResult:
        doc = extract_html(content, final_url)
        status = AdapterStatus.NOINDEX if doc.robots_meta.noindex else AdapterStatus.ACTIVE
        return AdapterResult(
            doc_type="page",
            status=status,
            document=doc,
            warnings=list(doc.warnings),
        )


class AdapterRegistry:
    """Registry of content adapters evaluated in priority order."""

    def __init__(self) -> None:
        self._sitemap = SitemapAdapter()
        self._feed = FeedAdapter()
        self._pdf = PdfAdapter()
        self._html = HtmlAdapter()
        self._text = PlainTextAdapter()

    def find_adapter(self, content_type: str, url: str) -> ContentAdapter | None:
        """Find the first adapter capable of handling the content type and URL."""
        # Check specific formats first, then generic HTML and text
        for candidate in (self._sitemap, self._feed, self._pdf, self._text, self._html):
            if candidate.can_handle(content_type, url):
                return candidate
        return None

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
    ) -> AdapterResult:
        """Extract content using the best matching adapter, or return unsupported."""
        ct = content_type or ""
        adapter = self.find_adapter(ct, final_url)

        if adapter is None:
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=["unsupported_content_type"],
            )
            return AdapterResult(
                doc_type="unknown",
                status=AdapterStatus.UNSUPPORTED,
                document=doc,
                warnings=["unsupported_content_type"],
            )

        try:
            return adapter.extract(content, final_url, content_type=ct)
        except Exception as exc:  # noqa: BLE001
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="",
                text="",
                warnings=[f"adapter_failure: {exc}"],
            )
            return AdapterResult(
                doc_type="unknown",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=[f"adapter_failure: {exc}"],
            )


_GLOBAL_REGISTRY = AdapterRegistry()


def extract_content(
    content: bytes,
    final_url: str,
    *,
    content_type: str | None = None,
) -> AdapterResult:
    """Convenience entry point for extracting content via the global adapter registry."""
    return _GLOBAL_REGISTRY.extract(content, final_url, content_type=content_type)
