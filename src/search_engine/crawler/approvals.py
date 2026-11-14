"""Unforgeable, single-use approval types for HTTP fetching (invariants I-1, I-8).

FetchApproval is issued for normal pages and images.
RobotsFetchApproval is bound to an exact robots.txt URL.
Both use a private sentinel so they cannot be forged without the scheduler factory.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from search_engine.errors import InvalidApproval

_SENTINEL = object()


class FetchApproval:
    """Approval token authorizing a single HTTP fetch of a normal resource or image."""

    def __init__(
        self,
        *,
        url: str,
        pinned_ip: str,
        host: str,
        max_bytes: int = 2 * 1024 * 1024,
        expected_content_types: tuple[str, ...] | None = None,
        kind: str = "page",
        _sentinel: object = None,
    ) -> None:
        if _sentinel is not _SENTINEL:
            raise InvalidApproval(
                "FetchApproval cannot be forged; use the scheduler issue_fetch_approval factory",
                code="approval_forged",
            )
        self.url = url
        self.pinned_ip = pinned_ip
        self.host = host
        self.max_bytes = max_bytes
        self.expected_content_types = expected_content_types
        self.kind = kind
        self._consumed = False

    @property
    def is_consumed(self) -> bool:
        return self._consumed

    def consume(self) -> None:
        """Mark approval as consumed. Raises if reused."""
        if self._consumed:
            raise InvalidApproval("FetchApproval has already been used", code="approval_reused")
        self._consumed = True


class RobotsFetchApproval:
    """Approval token authorizing a single fetch of a host's robots.txt."""

    def __init__(
        self,
        *,
        url: str,
        pinned_ip: str,
        host: str,
        max_bytes: int = 500 * 1024,
        _sentinel: object = None,
    ) -> None:
        if _sentinel is not _SENTINEL:
            raise InvalidApproval(
                "RobotsFetchApproval cannot be forged; use the scheduler factory",
                code="approval_forged",
            )
        self.url = url
        self.pinned_ip = pinned_ip
        self.host = host
        self.max_bytes = max_bytes
        self._consumed = False

    @property
    def is_consumed(self) -> bool:
        return self._consumed

    def consume(self) -> None:
        """Mark robots approval as consumed. Raises if reused."""
        if self._consumed:
            raise InvalidApproval(
                "RobotsFetchApproval has already been used", code="approval_reused"
            )
        self._consumed = True


def issue_fetch_approval(
    url: str,
    pinned_ip: str,
    *,
    host: str | None = None,
    max_bytes: int = 2 * 1024 * 1024,
    expected_content_types: tuple[str, ...] | None = None,
    kind: str = "page",
) -> FetchApproval:
    """Issue a single-use FetchApproval for a validated URL and pinned IP."""
    parsed = urlsplit(url)
    h = host or parsed.hostname
    if not h:
        raise InvalidApproval(f"URL missing hostname: {url}", code="invalid_approval")
    if not pinned_ip:
        raise InvalidApproval("pinned_ip must not be empty", code="invalid_approval")
    return FetchApproval(
        url=url,
        pinned_ip=pinned_ip,
        host=h,
        max_bytes=max_bytes,
        expected_content_types=expected_content_types,
        kind=kind,
        _sentinel=_SENTINEL,
    )


def issue_robots_approval(
    url: str,
    pinned_ip: str,
    *,
    host: str | None = None,
    max_bytes: int = 500 * 1024,
) -> RobotsFetchApproval:
    """Issue a single-use RobotsFetchApproval bound to an exact /robots.txt endpoint."""
    parsed = urlsplit(url)
    h = host or parsed.hostname
    if not h:
        raise InvalidApproval(f"URL missing hostname: {url}", code="invalid_approval")
    if not parsed.path.endswith("/robots.txt"):
        raise InvalidApproval(
            f"robots URL must end with /robots.txt (got {url})", code="invalid_approval"
        )
    if not pinned_ip:
        raise InvalidApproval("pinned_ip must not be empty", code="invalid_approval")
    return RobotsFetchApproval(
        url=url,
        pinned_ip=pinned_ip,
        host=h,
        max_bytes=max_bytes,
        _sentinel=_SENTINEL,
    )
