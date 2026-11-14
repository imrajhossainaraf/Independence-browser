"""RSS and Atom feed adapter using defusedxml."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any

import defusedxml.ElementTree as ET
from dateutil import parser as date_parser
from defusedxml.common import DefusedXmlException

from search_engine.crawler.normalize import normalize
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.extract.models import (
    AdapterResult,
    AdapterStatus,
    ExtractedDocument,
    NewsItemCandidate,
    OutgoingLink,
)

_WORDS = re.compile(r"\b\w+\b")


def _tag_name(element: Any) -> str:
    """Return local tag name stripping any XML namespace."""
    tag = getattr(element, "tag", "")
    return tag.split("}")[-1].lower() if "}" in tag else tag.lower()


def _get_child_text(element: Any, name: str) -> str:
    """Find immediate or namespaced child element text."""
    for child in element:
        if _tag_name(child) == name.lower():
            return (child.text or "").strip()
    return ""


def _parse_date(raw: str) -> str | None:
    if not raw.strip():
        return None
    try:
        parsed = date_parser.parse(raw.strip(), fuzzy=True)
        if 1970 <= parsed.year <= 2100:
            res: str = parsed.isoformat()
            return res
    except (ValueError, OverflowError):
        pass
    return None


class FeedAdapter:
    """Extracts feed metadata, news item candidates, and discovered links from RSS/Atom."""

    def can_handle(self, content_type: str, url: str) -> bool:
        lower_type = (content_type or "").lower().split(";")[0].strip()
        feed_types = {
            "application/rss+xml",
            "application/atom+xml",
            "application/feed+json",
            "application/xml",
            "text/xml",
        }
        if lower_type in feed_types:
            return True
        path = urllib.parse.urlsplit(url).path.lower()
        return path.endswith((".rss", ".atom", "/feed", "/rss"))

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
    ) -> AdapterResult:
        warnings: list[str] = []

        try:
            root = ET.fromstring(content)
        except DefusedXmlException as exc:
            warnings.append(f"xml_attack_forbidden: {exc}")
            return AdapterResult(
                doc_type="news",
                status=AdapterStatus.UNSUPPORTED,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"xml_parse_error: {exc}")
            return AdapterResult(
                doc_type="news",
                status=AdapterStatus.UNSUPPORTED,
                warnings=warnings,
            )

        root_tag = _tag_name(root)
        if root_tag in ("rss", "rdf"):
            return self._parse_rss(root, final_url, warnings)
        elif root_tag == "feed":
            return self._parse_atom(root, final_url, warnings)
        else:
            warnings.append(f"unrecognized_feed_root: {root_tag}")
            return AdapterResult(
                doc_type="news",
                status=AdapterStatus.UNSUPPORTED,
                warnings=warnings,
            )

    def _parse_rss(self, root: Any, final_url: str, warnings: list[str]) -> AdapterResult:
        channel = None
        for child in root:
            if _tag_name(child) == "channel":
                channel = child
                break
        if channel is None:
            channel = root

        feed_title = _get_child_text(channel, "title") or "RSS Feed"
        feed_desc = _get_child_text(channel, "description")
        feed_link_raw = _get_child_text(channel, "link")
        feed_lang = _get_child_text(channel, "language") or "und"
        if "-" in feed_lang:
            feed_lang = feed_lang.split("-")[0].lower()

        news_items: list[NewsItemCandidate] = []
        outgoing_links: list[OutgoingLink] = []
        body_snippets: list[str] = []

        for item in channel:
            if _tag_name(item) != "item":
                continue

            title = _get_child_text(item, "title")
            raw_link = _get_child_text(item, "link")
            raw_date = _get_child_text(item, "pubdate") or _get_child_text(item, "date")
            summary = _get_child_text(item, "description") or _get_child_text(item, "encoded")
            category = _get_child_text(item, "category")

            # Image extraction from enclosure or media tags
            image_url: str | None = None
            for sub in item:
                tag = _tag_name(sub)
                if tag == "enclosure":
                    enc_type = (sub.attrib.get("type") or "").lower()
                    if enc_type.startswith("image/") and sub.attrib.get("url"):
                        image_url = sub.attrib["url"].strip()
                        break
                elif tag in ("thumbnail", "content"):
                    if sub.attrib.get("url"):
                        m_type = (sub.attrib.get("type") or "").lower()
                        if not m_type or m_type.startswith("image/"):
                            image_url = sub.attrib["url"].strip()
                            break

            resolved_item_url = ""
            if raw_link:
                resolved = urllib.parse.urljoin(final_url, raw_link.strip())
                try:
                    norm = normalize(resolved)
                    resolved_item_url = norm.key
                    outgoing_links.append(
                        OutgoingLink(url=resolved_item_url, nofollow=False, anchor_text=title)
                    )
                except (UrlRejected, ValueError):
                    pass

            resolved_image_url: str | None = None
            if image_url:
                try:
                    norm_img = normalize(urllib.parse.urljoin(final_url, image_url))
                    if norm_img.scheme in ("http", "https"):
                        resolved_image_url = norm_img.key
                except (UrlRejected, ValueError):
                    pass

            published_at = _parse_date(raw_date) if raw_date else None
            news_items.append(
                NewsItemCandidate(
                    title=title,
                    link=resolved_item_url or raw_link,
                    published_at=published_at,
                    summary=summary,
                    category=category,
                    image_url=resolved_image_url,
                )
            )
            if title:
                body_snippets.append(f"{title}: {summary}" if summary else title)

        full_text = "\n\n".join(body_snippets)
        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        word_count = len(_WORDS.findall(full_text))

        doc = ExtractedDocument(
            final_url=final_url,
            canonical_url=feed_link_raw or None,
            title=feed_title,
            text=full_text,
            description=feed_desc,
            lang=feed_lang,
            outgoing_links=outgoing_links,
            content_hash=content_hash,
            word_count=word_count,
            warnings=warnings,
        )

        return AdapterResult(
            doc_type="news",
            status=AdapterStatus.ACTIVE,
            document=doc,
            news_items=news_items,
            warnings=warnings,
        )

    def _parse_atom(self, root: Any, final_url: str, warnings: list[str]) -> AdapterResult:
        feed_title = _get_child_text(root, "title") or "Atom Feed"
        feed_desc = _get_child_text(root, "subtitle")

        feed_link = ""
        for child in root:
            if _tag_name(child) == "link":
                rel = child.attrib.get("rel", "alternate")
                if rel in ("alternate", "") and child.attrib.get("href"):
                    feed_link = child.attrib["href"].strip()
                    break

        news_items: list[NewsItemCandidate] = []
        outgoing_links: list[OutgoingLink] = []
        body_snippets: list[str] = []

        for entry in root:
            if _tag_name(entry) != "entry":
                continue

            title = _get_child_text(entry, "title")
            raw_date = _get_child_text(entry, "published") or _get_child_text(entry, "updated")
            summary = _get_child_text(entry, "summary") or _get_child_text(entry, "content")
            category = ""
            for sub in entry:
                if _tag_name(sub) == "category" and sub.attrib.get("term"):
                    category = sub.attrib["term"]
                    break

            raw_link = ""
            image_url: str | None = None
            for sub in entry:
                tag = _tag_name(sub)
                if tag == "link":
                    rel = sub.attrib.get("rel", "alternate")
                    href = sub.attrib.get("href", "")
                    m_type = sub.attrib.get("type", "")
                    if rel == "enclosure" and m_type.startswith("image/"):
                        image_url = href.strip()
                    elif rel in ("alternate", "") and href and not raw_link:
                        raw_link = href.strip()
                elif tag in ("thumbnail", "content") and sub.attrib.get("url"):
                    image_url = sub.attrib["url"].strip()

            resolved_item_url = ""
            if raw_link:
                resolved = urllib.parse.urljoin(final_url, raw_link)
                try:
                    norm = normalize(resolved)
                    resolved_item_url = norm.key
                    outgoing_links.append(
                        OutgoingLink(url=resolved_item_url, nofollow=False, anchor_text=title)
                    )
                except (UrlRejected, ValueError):
                    pass

            resolved_image_url: str | None = None
            if image_url:
                try:
                    norm_img = normalize(urllib.parse.urljoin(final_url, image_url))
                    if norm_img.scheme in ("http", "https"):
                        resolved_image_url = norm_img.key
                except (UrlRejected, ValueError):
                    pass

            published_at = _parse_date(raw_date) if raw_date else None
            news_items.append(
                NewsItemCandidate(
                    title=title,
                    link=resolved_item_url or raw_link,
                    published_at=published_at,
                    summary=summary,
                    category=category,
                    image_url=resolved_image_url,
                )
            )
            if title:
                body_snippets.append(f"{title}: {summary}" if summary else title)

        full_text = "\n\n".join(body_snippets)
        content_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        word_count = len(_WORDS.findall(full_text))

        doc = ExtractedDocument(
            final_url=final_url,
            canonical_url=feed_link or None,
            title=feed_title,
            text=full_text,
            description=feed_desc,
            outgoing_links=outgoing_links,
            content_hash=content_hash,
            word_count=word_count,
            warnings=warnings,
        )

        return AdapterResult(
            doc_type="news",
            status=AdapterStatus.ACTIVE,
            document=doc,
            news_items=news_items,
            warnings=warnings,
        )
