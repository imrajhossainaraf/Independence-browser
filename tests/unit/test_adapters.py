"""Unit tests for content adapters (BUILD_PLAN Step 9)."""

from __future__ import annotations

import gzip
import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from search_engine.extract.adapters import (
    extract_content,
)
from search_engine.extract.feed import FeedAdapter
from search_engine.extract.models import AdapterResult, AdapterStatus
from search_engine.extract.pdf import PdfAdapter
from search_engine.extract.sitemap import SitemapAdapter
from search_engine.extract.text import PlainTextAdapter


def _make_pdf(
    text: str = "Search Engine PDF",
    num_pages: int = 1,
    *,
    encrypted: bool = False,
    blank: bool = False,
    title: str = "Test PDF Document",
) -> bytes:
    """Generate in-memory PDF bytes with selectable pages, text, or encryption."""
    writer = PdfWriter()
    for _ in range(num_pages):
        page = writer.add_blank_page(width=300, height=300)
        if not blank:
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 72 712 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = stream

            font_dict = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            fonts = DictionaryObject({NameObject("/F1"): font_dict})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})

    if title:
        writer.add_metadata({NameObject("/Title"): title})
    if encrypted:
        writer.encrypt("secret_pass")

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Plain Text Adapter Tests
# ---------------------------------------------------------------------------


def test_plain_text_adapter_small_text() -> None:
    """Plain text adapter extracts text, title from first line, and word count."""
    adapter = PlainTextAdapter()
    assert adapter.can_handle("text/plain", "https://example.com/notes.txt")
    assert adapter.can_handle("text/plain; charset=utf-8", "https://example.com/notes")

    content = b"Document Title Header\n\nThis is paragraph one.\nThis is paragraph two."
    res = adapter.extract(content, "https://example.com/notes.txt")

    assert isinstance(res, AdapterResult)
    assert res.doc_type == "text"
    assert res.status == AdapterStatus.ACTIVE
    assert res.document is not None
    assert res.document.title == "Document Title Header"
    assert "This is paragraph one." in res.document.text
    assert res.document.word_count > 0
    assert len(res.document.content_hash) == 64


def test_plain_text_adapter_empty_and_truncated() -> None:
    """Empty plain text yields metadata_only, and oversized text is truncated."""
    adapter = PlainTextAdapter()

    # Empty
    res_empty = adapter.extract(b"   \n\t  ", "https://example.com/empty.txt")
    assert res_empty.status == AdapterStatus.METADATA_ONLY
    assert "empty_text_content" in res_empty.warnings

    # Truncated
    long_content = b"A" * 500
    res_trunc = adapter.extract(long_content, "https://example.com/trunc.txt", max_bytes=100)
    assert "text_truncated_bytes" in res_trunc.warnings
    assert res_trunc.status == AdapterStatus.ACTIVE


# ---------------------------------------------------------------------------
# PDF Adapter Tests
# ---------------------------------------------------------------------------


def test_pdf_adapter_valid_searchable_pdf() -> None:
    """Searchable PDF extracts text and metadata in isolated worker."""
    adapter = PdfAdapter()
    assert adapter.can_handle("application/pdf", "https://example.com/manual.pdf")

    pdf_bytes = _make_pdf(text="Hello Search Engine PDF", title="Official Manual")
    res = adapter.extract(pdf_bytes, "https://example.com/manual.pdf")

    assert res.doc_type == "pdf"
    assert res.status == AdapterStatus.ACTIVE
    assert res.document is not None
    assert res.document.title == "Official Manual"
    assert "Hello Search Engine PDF" in res.document.text
    assert res.document.word_count > 0


def test_pdf_adapter_encrypted_degrades_to_metadata_only() -> None:
    """Encrypted PDF degrades gracefully to metadata_only with warning."""
    adapter = PdfAdapter()
    enc_bytes = _make_pdf(encrypted=True, title="Locked Document")
    res = adapter.extract(enc_bytes, "https://example.com/locked.pdf")

    assert res.doc_type == "pdf"
    assert res.status == AdapterStatus.METADATA_ONLY
    assert "encrypted_pdf" in res.warnings
    assert res.document is not None
    assert res.document.text == ""


def test_pdf_adapter_scanned_degrades_to_metadata_only() -> None:
    """Scanned/blank PDF with zero extractable text degrades to metadata_only."""
    adapter = PdfAdapter()
    blank_bytes = _make_pdf(blank=True, title="Scanned Page")
    res = adapter.extract(blank_bytes, "https://example.com/blank.pdf")

    assert res.doc_type == "pdf"
    assert res.status == AdapterStatus.METADATA_ONLY
    assert "scanned_pdf" in res.warnings
    assert res.document is not None
    assert res.document.text == ""


def test_pdf_adapter_malformed_corrupt_pdf() -> None:
    """Corrupted PDF degrades to metadata_only with a warning, never crashes."""
    adapter = PdfAdapter()
    corrupt_bytes = b"%PDF-1.4\n%corrupt truncated binary garbage\x00\xff\xfe"
    res = adapter.extract(corrupt_bytes, "https://example.com/corrupt.pdf")

    assert res.doc_type == "pdf"
    assert res.status == AdapterStatus.METADATA_ONLY
    assert any("pdf_read_error" in w for w in res.warnings)


def test_pdf_adapter_oversized_pdf_rejected() -> None:
    """PDF exceeding size limit is rejected without running the worker."""
    adapter = PdfAdapter()
    small_bytes = _make_pdf()
    res = adapter.extract(small_bytes, "https://example.com/oversized.pdf", max_bytes=50)

    assert res.status == AdapterStatus.METADATA_ONLY
    assert "pdf_size_limit_exceeded" in res.warnings


def test_pdf_adapter_page_limit_cap() -> None:
    """PDF exceeding page cap extracts up to limit and records warning."""
    adapter = PdfAdapter()
    multi_page_pdf = _make_pdf(text="Page content", num_pages=5)
    res = adapter.extract(multi_page_pdf, "https://example.com/multi.pdf", max_pages=2)

    assert res.status == AdapterStatus.ACTIVE
    assert "pdf_page_limit_exceeded" in res.warnings


def test_pdf_adapter_timeout_kills_worker_tree() -> None:
    """Subprocess hard timeout triggers tree kill and degrades to metadata_only."""
    adapter = PdfAdapter()
    pdf_bytes = _make_pdf()
    # Microscopic timeout forcing expiration
    res = adapter.extract(pdf_bytes, "https://example.com/slow.pdf", timeout=0.00001)

    assert res.status == AdapterStatus.METADATA_ONLY
    assert "pdf_timeout" in res.warnings


# ---------------------------------------------------------------------------
# Feed Adapter Tests (RSS & Atom)
# ---------------------------------------------------------------------------


def test_rss_feed_adapter_items_and_media() -> None:
    """RSS 2.0 feed extracts items, categories, discovered links, and media."""
    adapter = FeedAdapter()
    assert adapter.can_handle("application/rss+xml", "https://example.com/feed.xml")

    rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Technology News</title>
        <link>https://example.com</link>
        <description>Daily tech updates</description>
        <language>en-US</language>
        <item>
          <title>Quantum Chip Announced</title>
          <link>https://example.com/news/quantum</link>
          <pubDate>Wed, 15 Jul 2026 12:00:00 GMT</pubDate>
          <description>Researchers built a 1000-qubit processor.</description>
          <category>Hardware</category>
          <enclosure url="https://example.com/img/chip.jpg" type="image/jpeg" length="1234"/>
        </item>
      </channel>
    </rss>
    """
    res = adapter.extract(rss_xml.encode("utf-8"), "https://example.com/feed.xml")

    assert res.doc_type == "news"
    assert res.status == AdapterStatus.ACTIVE
    assert len(res.news_items) == 1
    item = res.news_items[0]
    assert item.title == "Quantum Chip Announced"
    assert item.link == "https://example.com/news/quantum"
    assert item.category == "Hardware"
    assert item.image_url == "https://example.com/img/chip.jpg"
    assert item.published_at is not None
    assert "2026-07-15" in item.published_at

    # Check discovered outgoing links
    assert res.document is not None
    assert any(
        link.url == "https://example.com/news/quantum" for link in res.document.outgoing_links
    )


def test_atom_feed_adapter_entries_and_media() -> None:
    """Atom feed extracts entries, alternate links, and media content."""
    adapter = FeedAdapter()
    assert adapter.can_handle("application/atom+xml", "https://example.com/atom")

    atom_xml = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Science Journal</title>
      <subtitle>Peer-reviewed updates</subtitle>
      <link rel="alternate" href="https://example.com/journal"/>
      <entry>
        <title>Exoplanet Atmosphere Discovered</title>
        <link rel="alternate" href="https://example.com/papers/exoplanet"/>
        <link rel="enclosure" type="image/png" href="https://example.com/planet.png"/>
        <published>2026-09-10T14:30:00Z</published>
        <summary>Spectroscopy revealed water vapor signatures.</summary>
        <category term="Astronomy"/>
      </entry>
    </feed>
    """
    res = adapter.extract(atom_xml.encode("utf-8"), "https://example.com/atom")

    assert res.doc_type == "news"
    assert res.status == AdapterStatus.ACTIVE
    assert len(res.news_items) == 1
    entry = res.news_items[0]
    assert entry.title == "Exoplanet Atmosphere Discovered"
    assert entry.link == "https://example.com/papers/exoplanet"
    assert entry.category == "Astronomy"
    assert entry.image_url == "https://example.com/planet.png"
    assert entry.published_at is not None
    assert "2026-09-10" in entry.published_at


# ---------------------------------------------------------------------------
# Sitemap Adapter Tests
# ---------------------------------------------------------------------------


def test_sitemap_adapter_urlset_and_index() -> None:
    """Sitemap adapter extracts URLs from both urlset and sitemapindex."""
    adapter = SitemapAdapter()
    assert adapter.can_handle("text/xml", "https://example.com/sitemap.xml")

    # Regular sitemap
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/page1</loc></url>
      <url><loc>https://example.com/page2</loc></url>
    </urlset>
    """
    res = adapter.extract(sitemap_xml.encode("utf-8"), "https://example.com/sitemap.xml")
    assert res.doc_type == "page"
    assert res.status == AdapterStatus.METADATA_ONLY
    assert "https://example.com/page1" in res.sitemap_urls
    assert "https://example.com/page2" in res.sitemap_urls

    # Sitemap index
    index_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/sitemap-news.xml</loc></sitemap>
    </sitemapindex>
    """
    res_idx = adapter.extract(index_xml.encode("utf-8"), "https://example.com/sitemap_index.xml")
    assert "https://example.com/sitemap-news.xml" in res_idx.sitemap_urls


def test_sitemap_adapter_gzipped_and_limit() -> None:
    """Gzipped sitemap is decompressed safely and capped at max_urls."""
    adapter = SitemapAdapter()

    # Generate 10 entries
    entries = "\n".join(f"<url><loc>https://example.com/item/{i}</loc></url>" for i in range(10))
    raw_xml = f'<?xml version="1.0"?><urlset>{entries}</urlset>'.encode()

    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb") as gz:
        gz.write(raw_xml)
    gz_bytes = gz_buf.getvalue()

    # Decompress and cap at 5 URLs
    res = adapter.extract(gz_bytes, "https://example.com/sitemap.xml.gz", max_urls=5)
    assert res.status == AdapterStatus.METADATA_ONLY
    assert len(res.sitemap_urls) == 5
    assert "sitemap_url_limit_exceeded" in res.warnings


def test_sitemap_adapter_gzip_bomb_stopped() -> None:
    """Gzip bomb is detected and halted by bounded decompression limit."""
    adapter = SitemapAdapter()

    # Compress 200 KB of zeros
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb") as gz:
        gz.write(b"0" * 200_000)
    gz_bytes = gz_buf.getvalue()

    # Cap output at 10 KB
    res = adapter.extract(
        gz_bytes, "https://example.com/bomb.xml.gz", max_uncompressed_bytes=10_000
    )
    assert res.status == AdapterStatus.METADATA_ONLY
    assert "gzip_bomb_stopped" in res.warnings


# ---------------------------------------------------------------------------
# XML Security Tests (Billion Laughs & External Entities)
# ---------------------------------------------------------------------------


def test_xml_security_billion_laughs_and_entities_rejected() -> None:
    """XML entity expansion and DOCTYPE attacks are safely blocked by defusedxml."""
    adapter = FeedAdapter()

    billion_laughs = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
     <!ENTITY lol "lol">
     <!ELEMENT lolz (#PCDATA)>
     <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    ]>
    <rss><channel><title>&lol1;</title></channel></rss>
    """
    res = adapter.extract(billion_laughs, "https://example.com/feed.xml")
    assert res.status == AdapterStatus.UNSUPPORTED
    assert any("xml_attack_forbidden" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# Dispatcher & Unsupported Tests
# ---------------------------------------------------------------------------


def test_unsupported_binary_format() -> None:
    """Unsupported binary content returns explicit unsupported status."""
    raw_bin = b"\x00\x01\x02\x03\x04\x05\x06"
    res = extract_content(
        raw_bin, "https://example.com/archive.bin", content_type="application/octet-stream"
    )

    assert res.doc_type == "unknown"
    assert res.status == AdapterStatus.UNSUPPORTED
    assert "unsupported_content_type" in res.warnings


def test_adapter_registry_dispatch() -> None:
    """Registry correctly routes HTML, text, PDF, feeds, and sitemaps."""
    # HTML
    res_html = extract_content(
        b"<html><head><title>Page</title></head><body><p>Text</p></body></html>",
        "https://example.com/page",
        content_type="text/html",
    )
    assert res_html.doc_type == "page"
    assert res_html.status == AdapterStatus.ACTIVE

    # Plain text
    res_txt = extract_content(
        b"First line title\nBody content",
        "https://example.com/file.txt",
        content_type="text/plain",
    )
    assert res_txt.doc_type == "text"
    assert res_txt.status == AdapterStatus.ACTIVE

    # PDF
    res_pdf = extract_content(
        _make_pdf(),
        "https://example.com/doc.pdf",
        content_type="application/pdf",
    )
    assert res_pdf.doc_type == "pdf"
    assert res_pdf.status == AdapterStatus.ACTIVE
