"""Unit tests for HTML document extraction (BUILD_PLAN Step 8)."""

from __future__ import annotations

import re
from pathlib import Path

from search_engine.extract.html import extract_html
from search_engine.extract.models import ExtractedDocument

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "html"
_WORDS = re.compile(r"\b\w+\b")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def _token_f1(extracted: str, gold: str) -> tuple[float, float, float]:
    ext_tokens = _tokenize(extracted)
    gold_tokens = _tokenize(gold)
    if not ext_tokens or not gold_tokens:
        return (0.0, 0.0, 0.0)

    ext_set = set(ext_tokens)
    gold_set = set(gold_tokens)
    intersection = ext_set & gold_set
    if not intersection:
        return (0.0, 0.0, 0.0)

    precision = len(intersection) / len(ext_set)
    recall = len(intersection) / len(gold_set)
    f1 = 2 * precision * recall / (precision + recall)
    return (precision, recall, f1)


def test_extraction_quality_against_20_gold_fixtures() -> None:
    """Validate extraction quality across 20 diverse fixtures, with 0 boilerplate leakage."""
    html_files = sorted(FIXTURES_DIR.glob("*.html"))
    assert len(html_files) == 20, f"Expected 20 fixtures, found {len(html_files)}"

    boilerplate_indicators = [
        "cookie policy",
        "all rights reserved",
        "advertisement",
        "sign in to your account",
    ]

    f1_scores: list[float] = []

    for html_path in html_files:
        gold_path = html_path.with_suffix(".gold.txt")
        assert gold_path.exists(), f"Missing gold file for {html_path.name}"

        html_content = html_path.read_text(encoding="utf-8")
        gold_text = gold_path.read_text(encoding="utf-8")

        doc = extract_html(html_content, final_url="https://example.com/test-page")
        assert isinstance(doc, ExtractedDocument)

        _, _, f1 = _token_f1(doc.text, gold_text)
        f1_scores.append(f1)

        # Check for boilerplate leakage
        lower_extracted = doc.text.lower()
        for indicator in boilerplate_indicators:
            assert indicator not in lower_extracted, (
                f"Boilerplate '{indicator}' leaked into extracted text for {html_path.name}"
            )

    avg_f1 = sum(f1_scores) / len(f1_scores)
    min_f1 = min(f1_scores)

    assert avg_f1 >= 0.90, f"Average F1 {avg_f1:.4f} is below threshold 0.90"
    assert min_f1 >= 0.80, f"Minimum F1 {min_f1:.4f} is below threshold 0.80"


def test_malformed_html_does_not_crash() -> None:
    """Malformed HTML yields a warning or empty doc, never an uncaught crash."""
    malformed_samples = [
        "<<<<html><<<body<<p>Unclosed garbage",
        "<a href='http://example.com'<<>>>weird tag",
        "<html><body><p>Valid text</p></div></span></td>",
        "<!DOCTYPE html><html><body>\x00\x01\x02\x03\xff\xfeBinary garbage</body></html>",
    ]
    for sample in malformed_samples:
        doc = extract_html(sample, final_url="https://example.com/malformed")
        assert isinstance(doc, ExtractedDocument)
        assert doc.final_url == "https://example.com/malformed"


def test_empty_html_handling() -> None:
    """Empty or whitespace HTML produces empty document with warning."""
    doc = extract_html("   \n\t  ", final_url="https://example.com/empty")
    assert doc.text == ""
    assert doc.title == ""
    assert "empty_html_input" in doc.warnings


def test_hostile_content_deep_nesting_and_huge_attributes() -> None:
    """Deep nesting and huge attributes neither crash nor hang."""
    deep_nesting = "<div>" * 1500 + "<p>Survivor text</p>" + "</div>" * 1500
    doc = extract_html(deep_nesting, final_url="https://example.com/nested")
    assert "Survivor text" in doc.text

    huge_attr = f'<div data-junk="{"A" * 80000}"><p>Content text</p></div>'
    doc2 = extract_html(huge_attr, final_url="https://example.com/huge-attr")
    assert "Content text" in doc2.text


def test_missing_title_warning_and_fallbacks() -> None:
    """Missing <title> falls back to og:title, twitter:title, h1, or records warning."""
    # 1. Fallback to og:title
    html1 = (
        '<html><head><meta property="og:title" content="OG Headline"></head>'
        "<body><p>Body</p></body></html>"
    )
    doc1 = extract_html(html1, final_url="https://example.com/page1")
    assert doc1.title == "OG Headline"

    # 2. Fallback to twitter:title
    html2 = (
        '<html><head><meta name="twitter:title" content="Twitter Headline"></head>'
        "<body><p>Body</p></body></html>"
    )
    doc2 = extract_html(html2, final_url="https://example.com/page2")
    assert doc2.title == "Twitter Headline"

    # 3. Fallback to h1
    html3 = "<html><body><h1>H1 Headline</h1><p>Body text here</p></body></html>"
    doc3 = extract_html(html3, final_url="https://example.com/page3")
    assert doc3.title == "H1 Headline"

    # 4. Totally missing title
    html4 = "<html><body><p>Just body text without headings</p></body></html>"
    doc4 = extract_html(html4, final_url="https://example.com/page4")
    assert doc4.title == ""
    assert "missing_title" in doc4.warnings


def test_canonical_url_resolution_and_normalization() -> None:
    """rel=canonical is resolved against final_url and normalized, invalid records warning."""
    # Valid relative canonical
    html1 = (
        '<html><head><link rel="canonical" href="/real-article?utm_source=twitter"></head>'
        "<body><p>Text</p></body></html>"
    )
    doc1 = extract_html(html1, final_url="https://example.com/sub/page")
    assert doc1.canonical_url == "https://example.com/real-article"

    # Invalid canonical scheme
    html2 = (
        '<html><head><link rel="canonical" href="javascript:void(0)"></head>'
        "<body><p>Text</p></body></html>"
    )
    doc2 = extract_html(html2, final_url="https://example.com/page")
    assert doc2.canonical_url is None
    assert "invalid_canonical_url" in doc2.warnings

    # Absent canonical
    html3 = "<html><head><title>Title</title></head><body><p>Text</p></body></html>"
    doc3 = extract_html(html3, final_url="https://example.com/page")
    assert doc3.canonical_url is None


def test_relative_links_and_nofollow_semantics() -> None:
    """Outgoing links are resolved, normalized, filtered, and respect nofollow."""
    html = """
    <html><body>
    <a href="/about">About Us</a>
    <a href="https://example.com/docs?b=2&a=1#section">Docs</a>
    <a href="https://partner.com/deal" rel="nofollow sponsored">Partner</a>
    <a href="javascript:alert(1)">Exploit</a>
    <a href="mailto:admin@example.com">Contact</a>
    <a href="#top">Anchor</a>
    </body></html>
    """
    doc = extract_html(html, final_url="https://example.com/main/index")
    urls = [link.url for link in doc.outgoing_links]
    assert "https://example.com/about" in urls
    assert "https://example.com/docs?a=1&b=2" in urls
    assert "https://partner.com/deal" in urls
    assert not any("javascript" in u for u in urls)
    assert not any("mailto" in u for u in urls)

    partner_link = next(link for link in doc.outgoing_links if "partner.com" in link.url)
    assert partner_link.nofollow is True

    about_link = next(link for link in doc.outgoing_links if "about" in link.url)
    assert about_link.nofollow is False
    assert about_link.anchor_text == "About Us"


def test_robots_meta_page_level_nofollow_and_noindex() -> None:
    """Page-level robots directives correctly set RobotsMeta and propagate nofollow."""
    html = """
    <html><head>
    <meta name="robots" content="noindex, nofollow, nosnippet">
    </head><body>
    <a href="/link1">Link 1</a>
    <p>Some secret article content.</p>
    </body></html>
    """
    doc = extract_html(html, final_url="https://example.com/secret")
    assert doc.robots_meta.noindex is True
    assert doc.robots_meta.nofollow is True
    assert doc.robots_meta.nosnippet is True
    assert doc.is_indexable is False

    # Page-level nofollow forces all outgoing links to nofollow=True
    assert len(doc.outgoing_links) == 1
    assert doc.outgoing_links[0].nofollow is True


def test_robots_meta_none_and_all() -> None:
    """none implies noindex and nofollow; all clears them."""
    html_none = (
        '<html><head><meta name="robots" content="none"></head><body><p>Text</p></body></html>'
    )
    doc_none = extract_html(html_none, final_url="https://example.com/none")
    assert doc_none.robots_meta.noindex is True
    assert doc_none.robots_meta.nofollow is True

    html_all = (
        '<html><head><meta name="robots" content="all"></head><body><p>Text</p></body></html>'
    )
    doc_all = extract_html(html_all, final_url="https://example.com/all")
    assert doc_all.robots_meta.noindex is False
    assert doc_all.robots_meta.nofollow is False


def test_multilingual_language_detection() -> None:
    """Language detection per D10: html lang, Content-Language, script heuristic, else und."""
    # 1. HTML lang attribute
    html_fr = '<html lang="fr-CA"><body><p>Bonjour le monde</p></body></html>'
    assert extract_html(html_fr, "https://example.com/fr").lang == "fr"

    # 2. Content-Language meta
    html_de = (
        '<html><head><meta http-equiv="Content-Language" content="de-DE"></head>'
        "<body><p>Guten Tag</p></body></html>"
    )
    assert extract_html(html_de, "https://example.com/de").lang == "de"

    # 3. Header parameter
    html_plain = "<html><body><p>Some text</p></body></html>"
    assert extract_html(html_plain, "https://example.com/es", content_language="es-ES").lang == "es"

    # 4. Cyrillic script heuristic
    cyrillic_body = (
        "<html><body><p>Это пример текста на русском языке без указания языка "
        "в тегах разметки документа.</p></body></html>"
    )
    assert extract_html(cyrillic_body, "https://example.com/ru").lang == "ru"

    # 5. CJK script heuristic
    cjk_body = "<html><body><p>这是没有任何语言标签的简体中文网页内容测试。</p></body></html>"
    assert extract_html(cjk_body, "https://example.com/zh").lang == "zh"

    # 6. Japanese Kana heuristic
    ja_body = (
        "<html><body><p>これは日本語のテスト文章です。"
        "ひらがなとカタカナが含まれています。</p></body></html>"
    )
    assert extract_html(ja_body, "https://example.com/ja").lang == "ja"

    # 7. Undecidable
    und_body = "<html><body><p>12345 67890</p></body></html>"
    assert extract_html(und_body, "https://example.com/und").lang == "und"


def test_published_date_extraction_variants() -> None:
    """Published date extraction from article meta, JSON-LD, and <time> tags."""
    # Article meta
    html1 = (
        '<html><head><meta property="article:published_time" '
        'content="2026-03-15T10:00:00Z"></head><body><p>News</p></body></html>'
    )
    doc1 = extract_html(html1, "https://example.com/p1")
    assert doc1.published_at is not None
    assert "2026-03-15" in doc1.published_at

    # JSON-LD
    html2 = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "datePublished": "2026-06-21T18:45:00Z"}
    </script>
    </head><body><p>News</p></body></html>
    """
    doc2 = extract_html(html2, "https://example.com/p2")
    assert doc2.published_at is not None
    assert "2026-06-21" in doc2.published_at

    # <time> element
    html3 = '<html><body><time datetime="2026-11-04">Nov 4, 2026</time><p>Story</p></body></html>'
    doc3 = extract_html(html3, "https://example.com/p3")
    assert doc3.published_at is not None
    assert "2026-11-04" in doc3.published_at


def test_media_references_and_image_candidates() -> None:
    """Media extraction captures images, captions, video, audio, transcripts, and candidates."""
    html = """
    <html><head>
    <meta property="og:image" content="https://example.com/images/hero.jpg">
    <meta name="twitter:image" content="/images/tw.png">
    </head><body>
    <figure>
        <img src="/media/photo.jpg" alt="A lovely landscape" title="Landscape">
        <figcaption>Figure 1: Landscape in spring</figcaption>
    </figure>
    <video title="Demo Video">
        <source src="/media/demo.mp4">
    </video>
    <audio title="Podcast Episode">
        <source src="/media/podcast.mp3">
    </audio>
    <a href="/transcripts/episode-1.html" rel="transcript">Read Episode Transcript</a>
    </body></html>
    """
    doc = extract_html(html, "https://example.com/page")

    # Media types
    types = [m.media_type for m in doc.media_refs]
    assert "image" in types
    assert "video" in types
    assert "audio" in types
    assert "transcript" in types

    img_ref = next(m for m in doc.media_refs if m.media_type == "image")
    assert img_ref.url == "https://example.com/media/photo.jpg"
    assert img_ref.alt == "A lovely landscape"
    assert img_ref.caption == "Figure 1: Landscape in spring"
    assert img_ref.title == "Landscape"

    # Image candidates
    assert "https://example.com/images/hero.jpg" in doc.image_candidates
    assert "https://example.com/images/tw.png" in doc.image_candidates


def test_deterministic_output_on_repeated_runs() -> None:
    """Repeated extraction on identical input produces identical fields and content_hash."""
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <title>Deterministic Test</title>
        <meta name="description" content="A deterministic test page">
        <link rel="canonical" href="https://example.com/canonical">
    </head>
    <body>
        <h1>Main Heading</h1>
        <h2>Subheading</h2>
        <p>This is deterministic paragraph text that produces identical hashes across runs.</p>
        <a href="/link1">Link 1</a>
    </body>
    </html>
    """
    doc1 = extract_html(html, "https://example.com/page")
    doc2 = extract_html(html, "https://example.com/page")

    assert doc1.title == doc2.title
    assert doc1.text == doc2.text
    assert doc1.content_hash == doc2.content_hash
    assert doc1.headings == doc2.headings
    assert doc1.word_count == doc2.word_count
    assert doc1.description == doc2.description
    assert doc1.canonical_url == doc2.canonical_url


def test_byte_input_and_max_bytes_truncation() -> None:
    """Byte inputs are decoded properly and oversized inputs are safely truncated."""
    content = b"<html><body><p>Hello from bytes!</p></body></html>"
    doc = extract_html(content, "https://example.com/bytes")
    assert "Hello from bytes!" in doc.text
    assert "html_truncated_bytes" not in doc.warnings

    # Tiny limit triggering truncation
    tiny_limit = 25
    doc_trunc = extract_html(content, "https://example.com/trunc", max_bytes=tiny_limit)
    assert "html_truncated_bytes" in doc_trunc.warnings

    # String input truncation
    long_str = "<html><body><p>" + "A" * 1000 + "</p></body></html>"
    doc_str_trunc = extract_html(long_str, "https://example.com/str-trunc", max_bytes=100)
    assert "html_truncated_bytes" in doc_str_trunc.warnings


def test_charset_meta_and_fallback_decoding() -> None:
    """Non-UTF-8 bytes with meta charset or fallback are decoded properly."""
    # Windows-1252 with smart quote / accent
    raw_1252 = (
        b'<html><head><meta charset="windows-1252"></head>'
        b"<body><p>Caf\xe9 and \x93quotes\x94</p></body></html>"
    )
    doc_1252 = extract_html(raw_1252, "https://example.com/cafewin")
    assert "Caf" in doc_1252.text

    # Charset normalizer fallback for non-UTF8 without meta
    raw_latin1 = b"<html><body><p>Sch\xf6ne Gr\xfc\xdfe</p></body></html>"
    doc_latin1 = extract_html(raw_latin1, "https://example.com/latin1")
    assert len(doc_latin1.text) > 0


def test_headings_text_and_hierarchy() -> None:
    """Headings h1-h6 are extracted in document order and accessible via headings_text."""
    html = """
    <html><body>
    <h1>Chapter 1</h1>
    <h2>Section 1.1</h2>
    <h3>Detail A</h3>
    <h4>Subdetail 1</h4>
    <h5>Note I</h5>
    <h6>Footnote alpha</h6>
    <p>Body paragraph.</p>
    </body></html>
    """
    doc = extract_html(html, "https://example.com/headings")
    assert len(doc.headings) == 6
    assert doc.headings[0] == "Chapter 1"
    assert doc.headings[-1] == "Footnote alpha"
    assert "Chapter 1 Section 1.1 Detail A" in doc.headings_text


def test_large_content_image_candidate_selection() -> None:
    """Content image candidate detects large images and skips logos/avatars."""
    html = """
    <html><body>
    <img src="/avatar.jpg" class="user-avatar" width="250" height="250">
    <img src="/logo.png" class="site-logo" width="400" height="100">
    <article>
        <img src="/hero-content.jpg" width="800" height="600" alt="Main hero">
    </article>
    </body></html>
    """
    doc = extract_html(html, "https://example.com/images-test")
    assert "https://example.com/hero-content.jpg" in doc.image_candidates
    assert not any("avatar.jpg" in url for url in doc.image_candidates)
    assert not any("logo.png" in url for url in doc.image_candidates)
