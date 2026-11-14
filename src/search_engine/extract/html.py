"""Deterministic HTML parsing and document extraction."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from typing import Any

import charset_normalizer
import trafilatura
from dateutil import parser as date_parser
from selectolax.lexbor import LexborHTMLParser

from search_engine.crawler.normalize import normalize
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.extract.models import (
    ExtractedDocument,
    MediaRef,
    OutgoingLink,
    RobotsMeta,
)

_WORDS = re.compile(r"\b\w+\b")
_REMOVE_SELECTOR = (
    "script, style, noscript, nav, header, footer, aside, form, svg, iframe, "
    "[role='dialog'], [id*='cookie'], [class*='cookie'], [id*='banner'], "
    "[class*='banner'], [class*='advertisement'], [id*='advertisement']"
)
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


def _clean_spaces(text: str) -> str:
    """Normalize and collapse consecutive whitespace characters."""
    return re.sub(r"\s+", " ", text).strip()


def _decode_html(content: str | bytes, max_bytes: int) -> tuple[str, list[str]]:
    """Decode raw input bytes or string into a safe string, capped at max_bytes."""
    warnings: list[str] = []
    if isinstance(content, bytes):
        if len(content) > max_bytes:
            content = content[:max_bytes]
            warnings.append("html_truncated_bytes")

        # 1. Try fast UTF-8
        try:
            return content.decode("utf-8"), warnings
        except UnicodeDecodeError:
            pass

        # 2. Check meta charset in the first 4KB
        sample = content[:4096]
        charset_match = re.search(
            rb"""<meta[^>]+(?:charset=["']?([a-zA-Z0-9_\-]+)|content=["'][^"']*charset=([a-zA-Z0-9_\-]+))""",
            sample,
            re.IGNORECASE,
        )
        if charset_match:
            raw_cs = charset_match.group(1) or charset_match.group(2)
            cs = raw_cs.decode("ascii", errors="ignore").strip()
            try:
                return content.decode(cs), warnings
            except (LookupError, UnicodeDecodeError):
                pass

        # 3. Charset normalizer detection
        best_match = charset_normalizer.from_bytes(content).best()
        if best_match is not None and best_match.encoding:
            try:
                return str(best_match), warnings
            except (ValueError, LookupError, UnicodeDecodeError):  # noqa: S110
                pass

        # 4. Fallback with replacement
        return content.decode("utf-8", errors="replace"), warnings

    # String input
    raw_encoded = content.encode("utf-8")
    if len(raw_encoded) > max_bytes:
        # Truncate at character boundary approximately
        content = content[:max_bytes]
        warnings.append("html_truncated_bytes")
    return content, warnings


def _extract_title(tree: LexborHTMLParser) -> str:
    """Extract page title with fallbacks to og:title, twitter:title, or first h1."""
    title_node = tree.css_first("head title") or tree.css_first("title")
    if title_node and title_node.text(strip=True):
        return _clean_spaces(title_node.text(strip=True))

    og_title = tree.css_first("meta[property='og:title'], meta[name='og:title']")
    if og_title:
        raw_og = og_title.attributes.get("content")
        if raw_og:
            return _clean_spaces(raw_og)

    tw_title = tree.css_first("meta[name='twitter:title']")
    if tw_title:
        raw_tw = tw_title.attributes.get("content")
        if raw_tw:
            return _clean_spaces(raw_tw)

    h1_node = tree.css_first("h1")
    if h1_node and h1_node.text(strip=True):
        return _clean_spaces(h1_node.text(separator=" ", strip=True))

    return ""


def _extract_canonical_url(tree: LexborHTMLParser, final_url: str) -> tuple[str | None, list[str]]:
    """Extract rel=canonical link, resolved against final_url and normalized."""
    warnings: list[str] = []
    canonical_node = tree.css_first("link[rel*='canonical']")
    if not canonical_node:
        return None, warnings

    raw_href = (canonical_node.attributes.get("href") or "").strip()
    if not raw_href:
        return None, warnings

    resolved = urllib.parse.urljoin(final_url, raw_href)
    try:
        norm = normalize(resolved)
        return norm.key, warnings
    except (UrlRejected, ValueError):
        warnings.append("invalid_canonical_url")
        return None, warnings


def _extract_robots_meta(tree: LexborHTMLParser) -> RobotsMeta:
    """Extract robots meta directives (noindex, nofollow, noarchive, nosnippet)."""
    noindex = False
    nofollow = False
    noarchive = False
    nosnippet = False

    for meta in tree.css(
        "meta[name*='robots' i], meta[name='googlebot' i], meta[name='bingbot' i]"
    ):
        content = (meta.attributes.get("content") or "").lower()
        directives = [d.strip() for d in content.split(",") if d.strip()]
        for directive in directives:
            if directive == "noindex":
                noindex = True
            elif directive == "nofollow":
                nofollow = True
            elif directive == "noarchive":
                noarchive = True
            elif directive == "nosnippet":
                nosnippet = True
            elif directive == "none":
                noindex = True
                nofollow = True
            elif directive == "all":
                noindex = False
                nofollow = False

    return RobotsMeta(
        noindex=noindex,
        nofollow=nofollow,
        noarchive=noarchive,
        nosnippet=nosnippet,
    )


def _detect_language(tree: LexborHTMLParser, text: str, content_language: str | None) -> str:
    """Detect document language per D10 (html lang, Content-Language, script heuristic)."""
    # 1. HTML lang attribute
    html_node = tree.css_first("html")
    if html_node:
        lang_attr = html_node.attributes.get("lang") or html_node.attributes.get("xml:lang")
        if lang_attr:
            primary = re.split(r"[-_]", lang_attr.strip())[0].lower()
            if re.match(r"^[a-z]{2,3}$", primary):
                return primary

    # 2. Meta http-equiv or Content-Language header
    meta_lang = tree.css_first("meta[http-equiv='content-language' i]")
    if meta_lang:
        meta_val = (meta_lang.attributes.get("content") or "").strip()
        if meta_val:
            primary = re.split(r"[-_, ]", meta_val)[0].lower()
            if re.match(r"^[a-z]{2,3}$", primary):
                return primary

    if content_language:
        primary = re.split(r"[-_, ]", content_language.strip())[0].lower()
        if re.match(r"^[a-z]{2,3}$", primary):
            return primary

    # 3. Unicode script heuristic
    sample_text = text[:4000]
    total_letters = sum(1 for ch in sample_text if ch.isalpha())
    if total_letters > 20:
        for pattern, code in _SCRIPT_PATTERNS:
            matches = len(pattern.findall(sample_text))
            if matches >= 10 and (matches / total_letters) > 0.20:
                return code

    return "und"


def _extract_published_date(tree: LexborHTMLParser) -> str | None:
    """Extract published date from article meta, JSON-LD, or <time> tags."""
    candidates: list[str] = []

    # Meta tags
    selectors = [
        "meta[property='article:published_time']",
        "meta[property='og:published_time']",
        "meta[name='pubdate']",
        "meta[name='publishdate']",
        "meta[name='date']",
        "meta[name='dc.date']",
        "meta[name='dc.date.issued']",
    ]
    for sel in selectors:
        for node in tree.css(sel):
            content = (node.attributes.get("content") or "").strip()
            if content:
                candidates.append(content)

    # JSON-LD scripts
    for script in tree.css("script[type='application/ld+json']"):
        raw_json = script.text(strip=True)
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
            _collect_json_dates(data, candidates)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    # <time> elements
    for time_node in tree.css("time"):
        dt = (time_node.attributes.get("datetime") or "").strip()
        if dt:
            candidates.append(dt)
        elif time_node.text(strip=True):
            candidates.append(time_node.text(strip=True))

    # Parse candidates
    for cand in candidates:
        try:
            parsed = date_parser.parse(cand, fuzzy=False)
            if 1970 <= parsed.year <= 2100:
                dt_str: str = parsed.isoformat()
                return dt_str
        except (ValueError, OverflowError):
            try:
                parsed = date_parser.parse(cand, fuzzy=True)
                if 1970 <= parsed.year <= 2100:
                    dt_str = parsed.isoformat()
                    return dt_str
            except (ValueError, OverflowError):
                continue

    return None


def _collect_json_dates(data: Any, out: list[str]) -> None:
    """Recursively search JSON-LD objects for datePublished/dateCreated/uploadDate."""
    if isinstance(data, dict):
        for k in ("datePublished", "dateCreated", "uploadDate"):
            val = data.get(k)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
        for v in data.values():
            _collect_json_dates(v, out)
    elif isinstance(data, list):
        for item in data:
            _collect_json_dates(item, out)


def _extract_headings(tree: LexborHTMLParser) -> list[str]:
    """Extract h1..h6 headings in document order."""
    headings: list[str] = []
    for node in tree.css("h1, h2, h3, h4, h5, h6"):
        text = _clean_spaces(node.text(separator=" ", strip=True))
        if text:
            headings.append(text)
    return headings


def _extract_links(
    tree: LexborHTMLParser,
    final_url: str,
    robots_meta: RobotsMeta,
) -> list[OutgoingLink]:
    """Extract outgoing links, resolved against final_url, normalized, with nofollow."""
    links: list[OutgoingLink] = []
    seen: set[str] = set()

    for a in tree.css("a[href]"):
        raw_href = (a.attributes.get("href") or "").strip()
        if not raw_href or raw_href.startswith("#"):
            continue

        # Skip non-web schemes
        lower_href = raw_href.lower()
        if lower_href.startswith(("javascript:", "mailto:", "tel:", "data:", "file:", "sms:")):
            continue

        resolved = urllib.parse.urljoin(final_url, raw_href)
        try:
            norm = normalize(resolved)
        except (UrlRejected, ValueError):
            continue

        rel = (a.attributes.get("rel") or "").lower().split()
        is_nofollow = robots_meta.nofollow or ("nofollow" in rel)

        if norm.key not in seen:
            seen.add(norm.key)
            anchor = _clean_spaces(a.text(separator=" ", strip=True))
            links.append(OutgoingLink(url=norm.key, nofollow=is_nofollow, anchor_text=anchor))

    return links


def _extract_media(tree: LexborHTMLParser, final_url: str) -> list[MediaRef]:
    """Extract references to images, video, audio, and transcript links."""
    media: list[MediaRef] = []

    # Images
    for img in tree.css("img[src]"):
        src = (img.attributes.get("src") or "").strip()
        if not src or src.lower().startswith("data:"):
            continue

        resolved = urllib.parse.urljoin(final_url, src)
        alt = _clean_spaces(img.attributes.get("alt") or "")
        title = _clean_spaces(img.attributes.get("title") or "")

        caption = ""
        parent = img.parent
        if parent and parent.tag == "figure":
            cap_node = parent.css_first("figcaption")
            if cap_node:
                caption = _clean_spaces(cap_node.text(separator=" ", strip=True))

        media.append(
            MediaRef(
                media_type="image",
                url=resolved,
                alt=alt,
                caption=caption,
                title=title,
            )
        )

    # Video
    for vid in tree.css("video"):
        src = (vid.attributes.get("src") or "").strip()
        if not src:
            source = vid.css_first("source[src]")
            if source:
                src = (source.attributes.get("src") or "").strip()
        vid_url = urllib.parse.urljoin(final_url, src) if src else None
        title = _clean_spaces(vid.attributes.get("title") or "")
        media.append(MediaRef(media_type="video", url=vid_url, title=title))

    # Audio
    for aud in tree.css("audio"):
        src = (aud.attributes.get("src") or "").strip()
        if not src:
            source = aud.css_first("source[src]")
            if source:
                src = (source.attributes.get("src") or "").strip()
        aud_url = urllib.parse.urljoin(final_url, src) if src else None
        title = _clean_spaces(aud.attributes.get("title") or "")
        media.append(MediaRef(media_type="audio", url=aud_url, title=title))

    # Transcripts
    for a in tree.css("a[href]"):
        raw_href = (a.attributes.get("href") or "").strip()
        rel = (a.attributes.get("rel") or "").lower()
        text = a.text(strip=True).lower()
        if "transcript" in rel or "transcript" in raw_href.lower() or "transcript" in text:
            resolved = urllib.parse.urljoin(final_url, raw_href)
            media.append(
                MediaRef(
                    media_type="transcript",
                    url=resolved,
                    title=_clean_spaces(a.text(separator=" ", strip=True)),
                )
            )

    return media


def _extract_image_candidates(tree: LexborHTMLParser, final_url: str) -> list[str]:
    """Extract image candidate URLs (og:image, twitter:image, image_src, first large img)."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(raw: str | None) -> None:
        if not raw:
            return
        trimmed = raw.strip()
        if not trimmed or trimmed.lower().startswith("data:"):
            return
        resolved = urllib.parse.urljoin(final_url, trimmed)
        try:
            norm = normalize(resolved)
        except (UrlRejected, ValueError):
            return
        if norm.key not in seen and norm.scheme in ("http", "https"):
            seen.add(norm.key)
            candidates.append(norm.key)

    # Social and meta tags
    meta_selectors = [
        "meta[property='og:image']",
        "meta[property='og:image:url']",
        "meta[name='twitter:image']",
        "meta[name='twitter:image:src']",
        "link[rel='image_src']",
    ]
    for sel in meta_selectors:
        for node in tree.css(sel):
            content = node.attributes.get("content") or node.attributes.get("href")
            add_candidate(content)

    # First large content image in body
    for img in tree.css("article img, main img, body img"):
        src = img.attributes.get("src")
        if not src:
            continue

        width_str = img.attributes.get("width")
        height_str = img.attributes.get("height")
        is_large = False
        if width_str and height_str:
            try:
                w = int(width_str)
                h = int(height_str)
                if w >= 200 and h >= 200:
                    is_large = True
            except ValueError:
                pass

        classes = (img.attributes.get("class") or "").lower()
        if any(skip in classes for skip in ("icon", "logo", "badge", "avatar", "spacer", "pixel")):
            continue

        if is_large or len(candidates) < 3:
            add_candidate(src)
            if len(candidates) >= 5:
                break

    return candidates


def _extract_readable_text(html_str: str) -> tuple[str, list[str]]:
    """Extract clean readable text using trafilatura with a selectolax fallback."""
    warnings: list[str] = []

    # 1. Trafilatura primary extraction
    trafilatura_text: str = ""
    try:
        res = trafilatura.extract(
            html_str,
            include_comments=False,
            include_tables=True,
            fast=True,
            favor_recall=True,
        )
        if res:
            trafilatura_text = res.strip()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"trafilatura_error: {exc}")

    # 2. Check token count for fallback decision
    tokens = [w.lower() for w in _WORDS.findall(trafilatura_text)]
    if len(tokens) < 40:
        # Run Selectolax Lexbor fallback
        try:
            tree = LexborHTMLParser(html_str)
            for tag in tree.css(_REMOVE_SELECTOR):
                tag.decompose()

            target = (
                tree.css_first("article")
                or tree.css_first("main")
                or tree.css_first("[role='main']")
                or tree.body
            )
            if target is not None:
                fallback_text = _clean_spaces(target.text(separator=" ", strip=True))
                fallback_tokens = [w.lower() for w in _WORDS.findall(fallback_text)]
                if len(fallback_tokens) > len(tokens):
                    warnings.append("trafilatura_fallback_used")
                    return fallback_text, warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"selectolax_fallback_error: {exc}")

    if not trafilatura_text:
        warnings.append("empty_body_text")

    return trafilatura_text, warnings


def extract_html(
    content: str | bytes,
    final_url: str,
    *,
    content_language: str | None = None,
    max_bytes: int = _MAX_BYTES_DEFAULT,
) -> ExtractedDocument:
    """Extract deterministic, searchable content from HTML.

    Never crashes on malformed, hostile, or oversized HTML; fails closed to warnings.
    """
    try:
        html_str, warnings = _decode_html(content, max_bytes)
    except Exception as exc:  # noqa: BLE001
        return ExtractedDocument(
            final_url=final_url,
            canonical_url=None,
            title="",
            text="",
            warnings=[f"decode_error: {exc}"],
        )

    if not html_str.strip():
        return ExtractedDocument(
            final_url=final_url,
            canonical_url=None,
            title="",
            text="",
            warnings=warnings + ["empty_html_input"],
        )

    try:
        tree = LexborHTMLParser(html_str)
    except Exception as exc:  # noqa: BLE001
        return ExtractedDocument(
            final_url=final_url,
            canonical_url=None,
            title="",
            text="",
            warnings=warnings + [f"parser_init_error: {exc}"],
        )

    # Metadata extraction
    title = _extract_title(tree)
    if not title:
        warnings.append("missing_title")

    canonical_url, can_warnings = _extract_canonical_url(tree, final_url)
    warnings.extend(can_warnings)

    desc_node = tree.css_first(
        "meta[name='description' i], "
        "meta[property='og:description' i], "
        "meta[name='twitter:description' i]"
    )
    raw_desc = desc_node.attributes.get("content") if desc_node else ""
    description = _clean_spaces(raw_desc or "")

    site_node = tree.css_first(
        "meta[property='og:site_name' i], meta[name='application-name' i], meta[name='publisher' i]"
    )
    raw_site = site_node.attributes.get("content") if site_node else ""
    site_name = _clean_spaces(raw_site or "")

    headings = _extract_headings(tree)
    robots_meta = _extract_robots_meta(tree)
    published_at = _extract_published_date(tree)
    outgoing_links = _extract_links(tree, final_url, robots_meta)
    media_refs = _extract_media(tree, final_url)
    image_candidates = _extract_image_candidates(tree, final_url)

    # Readable text extraction
    text, text_warnings = _extract_readable_text(html_str)
    warnings.extend(text_warnings)

    lang = _detect_language(tree, text, content_language)
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    word_count = len(_WORDS.findall(text))

    return ExtractedDocument(
        final_url=final_url,
        canonical_url=canonical_url,
        title=title,
        text=text,
        headings=headings,
        description=description,
        site_name=site_name,
        lang=lang,
        published_at=published_at,
        outgoing_links=outgoing_links,
        robots_meta=robots_meta,
        content_hash=content_hash,
        media_refs=media_refs,
        image_candidates=image_candidates,
        warnings=warnings,
        word_count=word_count,
    )
