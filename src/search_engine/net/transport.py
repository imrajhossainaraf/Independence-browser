"""Project-owned Transport protocol and data structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from search_engine.net.errors import ConnectError, TransportError
from search_engine.net.ip_policy import IPAddress, is_allowed_ip


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """Request specification passed to a Transport implementation."""

    method: str
    url: str
    pinned_ip: IPAddress | str
    server_name: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    connect_timeout: float = 10.0
    read_timeout: float = 20.0


@dataclass(slots=True)
class TransportResponse:
    """Response returned by a Transport implementation."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    peer_ip: str
    url: str

    def header(self, name: str, default: str | None = None) -> str | None:
        """Case-insensitive header lookup."""
        return self.headers.get(name.lower(), default)


class Transport(Protocol):
    """Protocol for sending HTTP requests to pinned IP destinations."""

    def send(
        self,
        request: TransportRequest,
        *,
        max_bytes: int | None = None,
    ) -> TransportResponse: ...

    def close(self) -> None: ...


class FakeTransport:
    """In-memory mock Transport for deterministic unit testing without sockets."""

    def __init__(self, *, allow_loopback: bool = True) -> None:
        self.allow_loopback = allow_loopback
        self.requests: list[TransportRequest] = []
        self.responses: dict[str, TransportResponse] = {}
        self.handlers: list[Callable[[TransportRequest], TransportResponse | None]] = []

    def register(self, url: str, response: TransportResponse) -> None:
        self.responses[url] = response

    def add_handler(self, handler: Callable[[TransportRequest], TransportResponse | None]) -> None:
        self.handlers.append(handler)

    def send(
        self,
        request: TransportRequest,
        *,
        max_bytes: int | None = None,
    ) -> TransportResponse:
        self.requests.append(request)

        # Enforce destination validation gate
        import ipaddress

        ip_obj = (
            request.pinned_ip
            if isinstance(request.pinned_ip, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            else ipaddress.ip_address(request.pinned_ip)
        )
        if not is_allowed_ip(ip_obj, allow_loopback=self.allow_loopback):
            raise ConnectError(
                f"FakeTransport refused connection to unallowed IP: {ip_obj}",
                code="address_not_allowed",
            )

        for handler in self.handlers:
            resp = handler(request)
            if resp is not None:
                return resp

        if request.url in self.responses:
            return self.responses[request.url]

        raise TransportError(f"no registered fake response for URL: {request.url}")

    def close(self) -> None:
        pass
