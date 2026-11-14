"""Single network choke point: Safe HTTP Fetcher (invariants I-1, I-4, I-5, I-7, I-8).

SafeFetcher downloads only bounded, approved, useful responses using a project Transport.
It accepts only unforgeable, single-use FetchApproval or RobotsFetchApproval tokens.
It never follows redirects automatically, checks content-types and magic bytes,
and classifies outcomes.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from search_engine.config import Settings
from search_engine.crawler.approvals import FetchApproval, RobotsFetchApproval
from search_engine.errors import (
    InvalidApproval,
    MimeTypeDisallowed,
    PayloadRejected,
    RedirectLimitExceeded,
    ResponseTooLargeError,
    StorageLimitExceeded,
)
from search_engine.net.errors import (
    ConnectError,
    ConnectTimeout,
    ReadTimeout,
    ResponseTooLarge,
    TimeoutError,
    TransportError,
)
from search_engine.net.transport import Transport, TransportRequest, TransportResponse

_SVG_TAG = re.compile(rb"<\s*svg[\s>]", re.IGNORECASE)
_ANIMATED_GIF_SIG = b"!\xf9\x04"


@dataclass(slots=True)
class Redirect:
    """A redirect encountered during fetching; returned to caller for re-approval."""

    target: str
    status_code: int
    headers: dict[str, str]


@dataclass(slots=True)
class FetchResult:
    """The outcome of an approved HTTP fetch."""

    url: str
    status_code: int
    outcome: str  # "success" | "retryable" | "permanent" | "rejected"
    content_type: str
    body: bytes
    headers: dict[str, str]
    peer_ip: str
    duration_seconds: float
    retry_after_seconds: float | None = None
    etag: str | None = None
    last_modified: str | None = None


def sniff_content(body: bytes, content_type: str) -> None:
    """Sniff magic bytes to reject executables, archives, SVG, and mislabeled binaries."""
    # 1. Reject Executables
    if body.startswith(b"MZ"):
        raise PayloadRejected("Windows/DOS executable (MZ) rejected", code="executable_rejected")
    if body.startswith(b"\x7fELF"):
        raise PayloadRejected("Linux ELF binary rejected", code="executable_rejected")
    if body.startswith(
        (
            b"\xfe\xed\xfa\xce",
            b"\xfe\xed\xfa\xcf",
            b"\xce\xfa\xed\xfe",
            b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe",
        )
    ):
        raise PayloadRejected("Mach-O or Java binary rejected", code="executable_rejected")

    # 2. Reject SVG images (Invariant I-5: downloaded content never executed; no SVG)
    ct_clean = content_type.split(";")[0].strip().lower()
    if ct_clean == "image/svg+xml" or _SVG_TAG.search(body[:2048]):
        raise PayloadRejected("SVG content is forbidden (invariant I-5)", code="svg_rejected")

    # 3. Reject Animated Images (Invariant I-5: no animated images)
    if body.startswith(b"GIF89a") and _ANIMATED_GIF_SIG in body:
        raise PayloadRejected("animated GIF is forbidden (invariant I-5)", code="animated_rejected")

    # 4. Reject Archives disguised as text or HTML
    if ct_clean in (
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "application/rss+xml",
        "application/atom+xml",
        "text/xml",
    ):
        if body.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            raise PayloadRejected("ZIP archive disguised as document", code="archive_rejected")
        if body.startswith(b"Rar!\x1a\x07"):
            raise PayloadRejected("RAR archive disguised as document", code="archive_rejected")
        if body.startswith(b"7z\xbc\xaf\x27\x1c"):
            raise PayloadRejected("7-Zip archive disguised as document", code="archive_rejected")

    # 5. Consistency check for binary formats
    if ct_clean == "application/pdf" and not body.startswith(b"%PDF-"):
        raise PayloadRejected("mislabeled PDF document", code="mislabeled_binary")
    if ct_clean == "image/jpeg" and not body.startswith(b"\xff\xd8\xff"):
        raise PayloadRejected("mislabeled JPEG image", code="mislabeled_binary")
    if ct_clean == "image/png" and not body.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PayloadRejected("mislabeled PNG image", code="mislabeled_binary")
    if ct_clean == "image/webp":
        if not (body.startswith(b"RIFF") and len(body) >= 12 and body[8:12] == b"WEBP"):
            raise PayloadRejected("mislabeled WebP image", code="mislabeled_binary")


class SafeFetcher:
    """Production safe fetcher implementing invariant I-1 single choke point."""

    def __init__(
        self,
        transport: Transport,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        storage_check: Callable[[], bool] | None = None,
    ) -> None:
        self.transport = transport
        self.settings = settings
        self._clock = clock
        self.storage_check = storage_check

    def _clean_content_type(self, raw_ct: str | None) -> str:
        if not raw_ct:
            return "application/octet-stream"
        return raw_ct.split(";")[0].strip().lower()

    def _parse_retry_after(self, val: str | None) -> float | None:
        if not val:
            return None
        val_clean = val.strip()
        if val_clean.isdigit():
            seconds = float(val_clean)
            return min(seconds, self.settings.max_crawl_delay_seconds)
        return None

    def fetch_once(self, approval: FetchApproval) -> FetchResult | Redirect:
        """Fetch a normal page or image using a valid FetchApproval."""
        if not isinstance(approval, FetchApproval) or isinstance(approval, RobotsFetchApproval):
            raise InvalidApproval(
                "fetch_once requires a FetchApproval (got robots or unknown approval)",
                code="wrong_approval_type",
            )
        approval.consume()

        if self.storage_check is not None and not self.storage_check():
            raise StorageLimitExceeded(
                "total storage limit exceeded", code="storage_limit_exceeded"
            )

        parsed = urlsplit(approval.url)
        if parsed.hostname != approval.host:
            raise InvalidApproval(
                f"approval host {approval.host} does not match URL host {parsed.hostname}",
                code="host_mismatch",
            )

        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept-Encoding": "gzip, deflate",
        }
        if approval.kind == "image":
            headers["Accept"] = "image/webp,image/png,image/jpeg;q=0.9,*/*;q=0.1"
        else:
            headers["Accept"] = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1"

        req = TransportRequest(
            method="GET",
            url=approval.url,
            pinned_ip=approval.pinned_ip,
            server_name=approval.host,
            headers=headers,
            connect_timeout=self.settings.connect_timeout_seconds,
            read_timeout=self.settings.read_timeout_seconds,
        )

        start = self._clock()
        try:
            resp: TransportResponse = self.transport.send(req, max_bytes=approval.max_bytes)
        except ResponseTooLarge as exc:
            raise ResponseTooLargeError(str(exc), code="response_too_large") from exc
        except (ConnectTimeout, ReadTimeout, TimeoutError, ConnectError, TransportError):
            elapsed = self._clock() - start
            return FetchResult(
                url=approval.url,
                status_code=0,
                outcome="retryable",
                content_type="",
                body=b"",
                headers={},
                peer_ip=str(approval.pinned_ip),
                duration_seconds=elapsed,
            )

        elapsed = self._clock() - start

        # Check for redirects
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if loc:
                target = urljoin(approval.url, loc)
                return Redirect(target=target, status_code=resp.status_code, headers=resp.headers)

        ct = self._clean_content_type(resp.headers.get("content-type"))

        # Status classification & content verification
        if 200 <= resp.status_code <= 299:
            # Check content-type allowlist
            allowed_types = approval.expected_content_types or self.settings.allowed_content_types
            if approval.kind == "image":
                allowed_types = ("image/jpeg", "image/png", "image/webp")

            if ct not in allowed_types:
                raise MimeTypeDisallowed(
                    f"Content-Type {ct!r} not in allowed types: {allowed_types}",
                    code="mime_type_disallowed",
                )

            # Magic byte sniffing
            sniff_content(resp.body, ct)

            return FetchResult(
                url=approval.url,
                status_code=resp.status_code,
                outcome="success",
                content_type=ct,
                body=resp.body,
                headers=resp.headers,
                peer_ip=resp.peer_ip,
                duration_seconds=elapsed,
                etag=resp.headers.get("etag"),
                last_modified=resp.headers.get("last-modified"),
            )

        if resp.status_code == 429:
            retry_after = self._parse_retry_after(resp.headers.get("retry-after"))
            return FetchResult(
                url=approval.url,
                status_code=resp.status_code,
                outcome="retryable",
                content_type=ct,
                body=resp.body,
                headers=resp.headers,
                peer_ip=resp.peer_ip,
                duration_seconds=elapsed,
                retry_after_seconds=retry_after,
            )

        if resp.status_code in (408, 425) or 500 <= resp.status_code <= 599:
            return FetchResult(
                url=approval.url,
                status_code=resp.status_code,
                outcome="retryable",
                content_type=ct,
                body=resp.body,
                headers=resp.headers,
                peer_ip=resp.peer_ip,
                duration_seconds=elapsed,
            )

        # Other 4xx status codes are permanent errors
        return FetchResult(
            url=approval.url,
            status_code=resp.status_code,
            outcome="permanent",
            content_type=ct,
            body=resp.body,
            headers=resp.headers,
            peer_ip=resp.peer_ip,
            duration_seconds=elapsed,
        )

    def fetch_robots(self, approval: RobotsFetchApproval) -> FetchResult | Redirect:
        """Fetch a robots.txt resource using a valid RobotsFetchApproval."""
        if not isinstance(approval, RobotsFetchApproval):
            raise InvalidApproval(
                "fetch_robots requires a RobotsFetchApproval",
                code="wrong_approval_type",
            )
        approval.consume()

        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/plain,*/*;q=0.1",
            "Accept-Encoding": "gzip, deflate",
        }

        req = TransportRequest(
            method="GET",
            url=approval.url,
            pinned_ip=approval.pinned_ip,
            server_name=approval.host,
            headers=headers,
            connect_timeout=self.settings.connect_timeout_seconds,
            read_timeout=self.settings.read_timeout_seconds,
        )

        start = self._clock()
        try:
            resp: TransportResponse = self.transport.send(req, max_bytes=approval.max_bytes)
        except ResponseTooLarge as exc:
            raise ResponseTooLargeError(str(exc), code="response_too_large") from exc
        except (ConnectTimeout, ReadTimeout, TimeoutError, ConnectError, TransportError):
            elapsed = self._clock() - start
            return FetchResult(
                url=approval.url,
                status_code=0,
                outcome="retryable",
                content_type="",
                body=b"",
                headers={},
                peer_ip=str(approval.pinned_ip),
                duration_seconds=elapsed,
            )

        elapsed = self._clock() - start

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if loc:
                target = urljoin(approval.url, loc)
                return Redirect(target=target, status_code=resp.status_code, headers=resp.headers)

        ct = self._clean_content_type(resp.headers.get("content-type"))
        outcome = (
            "success"
            if 200 <= resp.status_code <= 299
            else (
                "retryable"
                if resp.status_code in (408, 425, 429) or resp.status_code >= 500
                else "permanent"
            )
        )
        retry_after = (
            self._parse_retry_after(resp.headers.get("retry-after"))
            if resp.status_code == 429
            else None
        )

        return FetchResult(
            url=approval.url,
            status_code=resp.status_code,
            outcome=outcome,
            content_type=ct,
            body=resp.body,
            headers=resp.headers,
            peer_ip=resp.peer_ip,
            duration_seconds=elapsed,
            retry_after_seconds=retry_after,
        )

    def follow(
        self,
        approve_callback: Callable[[str, int], FetchApproval],
        start_url: str,
        *,
        max_redirects: int = 5,
    ) -> FetchResult:
        """Drive redirect hops up to max_redirects, re-approving every hop via callback."""
        current_url = start_url
        for hop in range(max_redirects + 1):
            approval = approve_callback(current_url, hop)
            res = self.fetch_once(approval)
            if isinstance(res, Redirect):
                current_url = res.target
                continue
            return res
        raise RedirectLimitExceeded(
            f"exceeded maximum of {max_redirects} redirects", code="too_many_redirects"
        )
