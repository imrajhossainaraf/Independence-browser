"""Content extraction and adapters."""

from search_engine.extract.adapters import (
    AdapterRegistry,
    ContentAdapter,
    HtmlAdapter,
    extract_content,
)
from search_engine.extract.feed import FeedAdapter
from search_engine.extract.html import extract_html
from search_engine.extract.models import (
    AdapterResult,
    AdapterStatus,
    ExtractedDocument,
    MediaRef,
    NewsItemCandidate,
    OutgoingLink,
    RobotsMeta,
    SitemapUrl,
)
from search_engine.extract.pdf import PdfAdapter
from search_engine.extract.sitemap import SitemapAdapter
from search_engine.extract.text import PlainTextAdapter

__all__ = [
    "AdapterRegistry",
    "AdapterResult",
    "AdapterStatus",
    "ContentAdapter",
    "ExtractedDocument",
    "FeedAdapter",
    "HtmlAdapter",
    "MediaRef",
    "NewsItemCandidate",
    "OutgoingLink",
    "PdfAdapter",
    "PlainTextAdapter",
    "RobotsMeta",
    "SitemapAdapter",
    "SitemapUrl",
    "extract_content",
    "extract_html",
]
