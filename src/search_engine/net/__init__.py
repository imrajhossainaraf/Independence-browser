"""Network layer: the only package allowed to import an HTTP client (invariant I-1)."""

from __future__ import annotations

from search_engine.net.errors import (
    ConnectError,
    ConnectTimeout,
    InvalidAddressError,
    NetworkError,
    ReadTimeout,
    ResolutionError,
    ResponseTooLarge,
    TimeoutError,
    TransportError,
)
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.ip_policy import (
    IPAddress,
    is_allowed_ip,
    is_loopback_ip,
    is_public_ip,
    parse_ipv4_like,
    parse_ipv6,
    validate_resolved_ips,
)
from search_engine.net.resolver import FakeResolver, Resolver, SystemResolver
from search_engine.net.transport import (
    FakeTransport,
    Transport,
    TransportRequest,
    TransportResponse,
)

__all__ = [
    "ConnectError",
    "ConnectTimeout",
    "FakeResolver",
    "FakeTransport",
    "HttpxTransport",
    "IPAddress",
    "InvalidAddressError",
    "NetworkError",
    "ReadTimeout",
    "ResolutionError",
    "Resolver",
    "ResponseTooLarge",
    "SystemResolver",
    "TimeoutError",
    "Transport",
    "TransportError",
    "TransportRequest",
    "TransportResponse",
    "is_allowed_ip",
    "is_loopback_ip",
    "is_public_ip",
    "parse_ipv4_like",
    "parse_ipv6",
    "validate_resolved_ips",
]
