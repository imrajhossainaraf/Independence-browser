"""XML Sitemap and Sitemap Index adapter using defusedxml with bounded decompression."""

from __future__ import annotations

import gzip
import io
import urllib.parse
from typing import Any

import defusedxml.ElementTree as ET
from defusedxml.common import DefusedXmlException

from search_engine.crawler.normalize import normalize
from search_engine.crawler.urlsafety import UrlRejected
from search_engine.extract.models import (
    AdapterResult,
    AdapterStatus,
    ExtractedDocument,
    OutgoingLink,
)

_MAX_SITEMAP_URLS = 5000  # D10 ceiling: 5,000 URLs per sitemap file
_MAX_UNCOMPRESSED_BYTES = 10 * 1024 * 1024  # 10 MiB cap to stop gzip bombs


def _tag_name(element: Any) -> str:
    """Return local tag name stripping XML namespace."""
    tag = getattr(element, "tag", "")
    return tag.split("}")[-1].lower() if "}" in tag else tag.lower()


def _decompress_bounded(content: bytes, max_bytes: int) -> tuple[bytes, list[str]]:
    """Decompress gzip content with a strict output ceiling."""
    warnings: list[str] = []
    if content.startswith(b"\x1f\x8b"):
        bio = io.BytesIO(content)
        chunks: list[bytes] = []
        total = 0
        try:
            with gzip.GzipFile(fileobj=bio, mode="rb") as gz:
                while chunk := gz.read(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        warnings.append("gzip_bomb_stopped")
                        break
                    chunks.append(chunk)
            return b"".join(chunks), warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"gzip_decompress_error: {exc}")
            return b"", warnings
    return content, warnings


class SitemapAdapter:
    """Extracts discovered URLs from XML sitemaps and sitemap indexes."""

    def can_handle(self, content_type: str, url: str) -> bool:
        lower_type = (content_type or "").lower().split(";")[0].strip()
        path = urllib.parse.urlsplit(url).path.lower()
        if "sitemap" in path or path.endswith((".xml", ".xml.gz")):
            return True
        if lower_type in ("application/xml", "text/xml"):
            return "sitemap" in url.lower()
        return False

    def extract(
        self,
        content: bytes,
        final_url: str,
        *,
        content_type: str | None = None,
        max_urls: int = _MAX_SITEMAP_URLS,
        max_uncompressed_bytes: int = _MAX_UNCOMPRESSED_BYTES,
    ) -> AdapterResult:
        warnings: list[str] = []

        raw_xml, decomp_warnings = _decompress_bounded(content, max_uncompressed_bytes)
        warnings.extend(decomp_warnings)

        if "gzip_bomb_stopped" in warnings or not raw_xml:
            doc = ExtractedDocument(
                final_url=final_url,
                canonical_url=None,
                title="Sitemap (Stopped)",
                text="",
                warnings=warnings,
            )
            return AdapterResult(
                doc_type="page",
                status=AdapterStatus.METADATA_ONLY,
                document=doc,
                warnings=warnings,
            )

        try:
            root = ET.fromstring(raw_xml)
        except DefusedXmlException as exc:
            warnings.append(f"xml_attack_forbidden: {exc}")
            return AdapterResult(
                doc_type="page",
                status=AdapterStatus.UNSUPPORTED,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"xml_parse_error: {exc}")
            return AdapterResult(
                doc_type="page",
                status=AdapterStatus.UNSUPPORTED,
                warnings=warnings,
            )

        root_tag = _tag_name(root)
        if root_tag not in ("urlset", "sitemapindex"):
            warnings.append(f"unrecognized_sitemap_root: {root_tag}")
            return AdapterResult(
                doc_type="page",
                status=AdapterStatus.UNSUPPORTED,
                warnings=warnings,
            )

        discovered_urls: list[str] = []
        outgoing_links: list[OutgoingLink] = []
        seen: set[str] = set()

        for child in root:
            c_tag = _tag_name(child)
            if c_tag in ("url", "sitemap"):
                loc_text = ""
                for prop in child:
                    if _tag_name(prop) == "loc":
                        loc_text = (prop.text or "").strip()
                        break

                if loc_text:
                    resolved = urllib.parse.urljoin(final_url, loc_text)
                    try:
                        norm = normalize(resolved)
                        if norm.key not in seen:
                            seen.add(norm.key)
                            discovered_urls.append(norm.key)
                            outgoing_links.append(
                                OutgoingLink(url=norm.key, nofollow=False, anchor_text="")
                            )
                            if len(discovered_urls) >= max_urls:
                                warnings.append("sitemap_url_limit_exceeded")
                                break
                    except (UrlRejected, ValueError):
                        pass

        is_index = root_tag == "sitemapindex"
        title = (
            f"Sitemap Index ({len(discovered_urls)} sitemaps)"
            if is_index
            else f"Sitemap ({len(discovered_urls)} urls)"
        )

        doc = ExtractedDocument(
            final_url=final_url,
            canonical_url=None,
            title=title,
            text="",
            outgoing_links=outgoing_links,
            warnings=warnings,
        )

        return AdapterResult(
            doc_type="page",
            status=AdapterStatus.METADATA_ONLY,
            document=doc,
            sitemap_urls=discovered_urls,
            warnings=warnings,
        )
