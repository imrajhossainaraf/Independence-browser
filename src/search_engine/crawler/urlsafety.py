"""String-level URL safety (decision D2): reason codes and host checks. No DNS, no network."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from search_engine.errors import PolicyDenied
from search_engine.net.ip_policy import (
    IPAddress,
    is_loopback_ip,
    is_public_ip,
    parse_ipv4_like,
    parse_ipv6,
)


class UrlReason(StrEnum):
    EMPTY = "empty"
    TOO_LONG = "too_long"
    CONTROL_CHARS = "control_chars"
    MALFORMED = "malformed"
    BAD_SCHEME = "bad_scheme"
    CREDENTIALS = "credentials"
    BAD_PORT = "bad_port"
    BAD_HOST = "bad_host"
    UNSAFE_HOST = "unsafe_host"
    NON_PUBLIC_IP = "non_public_ip"
    NO_REGISTERED_DOMAIN = "no_registered_domain"


class UrlRejected(PolicyDenied):
    """A URL that must never enter the system. ``reason`` is a stable code."""

    def __init__(self, reason: UrlReason, message: str | None = None) -> None:
        super().__init__(message or reason.value, code=f"url_{reason.value}")
        self.reason = reason


_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_FORBIDDEN_SUFFIXES = (".localhost", ".local", ".internal")
_FORBIDDEN_NAMES = frozenset({"localhost", "local", "internal"})


@dataclass(frozen=True, slots=True)
class HostInfo:
    host: str  # canonical text used in the normalized URL (IPv6 without brackets)
    ip: IPAddress | None


def _idna(host: str) -> str:
    if host.isascii():
        return host.lower()
    try:
        return host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UrlRejected(UrlReason.BAD_HOST, "host cannot be converted to punycode") from exc


def _check_ip(ip: IPAddress, *, allow_loopback: bool) -> None:
    if is_public_ip(ip):
        return
    if allow_loopback and is_loopback_ip(ip):
        return
    raise UrlRejected(UrlReason.NON_PUBLIC_IP, "address is not public")


def check_host(host: str, *, bracketed: bool, allow_loopback: bool = False) -> HostInfo:
    """Validate and canonicalize a host. Raises UrlRejected for anything unsafe or malformed."""
    if bracketed:
        try:
            ip6 = parse_ipv6(host)
        except ValueError as exc:
            raise UrlRejected(UrlReason.BAD_HOST, "invalid IPv6 literal") from exc
        _check_ip(ip6, allow_loopback=allow_loopback)
        return HostInfo(str(ip6), ip6)

    if host.endswith("."):
        host = host[:-1]
    if not host or host.startswith("."):
        raise UrlRejected(UrlReason.BAD_HOST, "empty host")
    host = _idna(host)
    if len(host) > 253:
        raise UrlRejected(UrlReason.BAD_HOST, "host too long")

    try:
        ip4 = parse_ipv4_like(host)
    except ValueError as exc:
        raise UrlRejected(UrlReason.BAD_HOST, "ambiguous numeric host") from exc
    if ip4 is not None:
        _check_ip(ip4, allow_loopback=allow_loopback)
        return HostInfo(str(ip4), ip4)

    if ":" in host:
        raise UrlRejected(UrlReason.BAD_HOST, "unexpected colon in host")
    is_local_name = host in _FORBIDDEN_NAMES or host.endswith(_FORBIDDEN_SUFFIXES)
    if is_local_name:
        is_localhost = host == "localhost" or host.endswith(".localhost")
        if not (allow_loopback and is_localhost):
            raise UrlRejected(UrlReason.UNSAFE_HOST, "local or internal host name")
    for label in host.split("."):
        if not _LABEL.match(label):
            raise UrlRejected(UrlReason.BAD_HOST, "invalid host label")
    return HostInfo(host, None)
