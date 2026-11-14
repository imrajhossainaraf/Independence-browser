"""Single production transport implementation using httpx (invariants I-1, I-3, decision D2).

This is the only module in the project that imports httpx.
Connections are strictly pinned to a validated IP, with SNI and Host set to the original hostname.
Keep-alive is disabled (max_keepalive_connections=0) to prevent connection pool
cross-host reuse (ADR-006).
"""

from __future__ import annotations

import ipaddress
import ssl
from typing import Self
from urllib.parse import urlsplit

import httpx

from search_engine.net.errors import (
    ConnectError,
    ConnectTimeout,
    InvalidAddressError,
    ReadTimeout,
    ResponseTooLarge,
    TimeoutError,
    TransportError,
)
from search_engine.net.ip_policy import IPAddress, is_allowed_ip
from search_engine.net.transport import TransportRequest, TransportResponse


def _format_ip_for_url(ip: IPAddress | str) -> str:
    s = str(ip)
    return f"[{s}]" if ":" in s else s


class HttpxTransport:
    """Production HTTP transport pinning requests to pre-validated IP addresses."""

    def __init__(
        self,
        *,
        allow_loopback: bool = False,
        verify: ssl.SSLContext | str | bool = True,
    ) -> None:
        self._allow_loopback = allow_loopback
        self._verify = verify
        # Keep-alive disabled so every request opens a fresh connection with its own TLS handshake.
        self._limits = httpx.Limits(max_keepalive_connections=0, max_connections=None)
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                verify=self._verify,
                limits=self._limits,
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    def send(
        self,
        request: TransportRequest,
        *,
        max_bytes: int | None = None,
    ) -> TransportResponse:
        # 1. Gate: Validate the pinned IP immediately before connecting
        ip_obj = (
            request.pinned_ip
            if isinstance(request.pinned_ip, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            else ipaddress.ip_address(request.pinned_ip)
        )
        if not is_allowed_ip(ip_obj, allow_loopback=self._allow_loopback):
            raise InvalidAddressError(
                f"refusing connection to unallowed IP address: {ip_obj}",
                code="address_not_allowed",
            )

        # 2. Parse URL and construct pinned endpoint URL
        parsed = urlsplit(request.url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https"):
            raise TransportError(f"unsupported scheme: {scheme}", code="unsupported_scheme")

        hostname = parsed.hostname or ""
        server_name = request.server_name or hostname

        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80

        path_and_query = parsed.path or "/"
        if parsed.query:
            path_and_query += f"?{parsed.query}"

        pinned_target = f"{scheme}://{_format_ip_for_url(ip_obj)}:{port}{path_and_query}"

        # 3. Construct headers and TLS extensions
        host_header = (
            server_name
            if (port == 80 and scheme == "http") or (port == 443 and scheme == "https")
            else f"{server_name}:{port}"
        )
        headers = dict(request.headers)
        headers["Host"] = host_header

        extensions: dict[str, object] = {}
        if scheme == "https":
            extensions["sni_hostname"] = server_name

        timeout = httpx.Timeout(
            connect=request.connect_timeout,
            read=request.read_timeout,
            write=10.0,
            pool=5.0,
        )

        client = self._get_client()

        try:
            with client.stream(
                method=request.method,
                url=pinned_target,
                headers=headers,
                content=request.body,
                extensions=extensions,
                timeout=timeout,
                follow_redirects=False,
            ) as stream_resp:
                body_parts: list[bytes] = []
                total_bytes = 0

                for chunk in stream_resp.iter_bytes():
                    total_bytes += len(chunk)
                    if max_bytes is not None and total_bytes > max_bytes:
                        raise ResponseTooLarge(
                            f"response exceeded max_bytes={max_bytes} (read {total_bytes} bytes)",
                            code="response_too_large",
                        )
                    body_parts.append(chunk)

                resp_body = b"".join(body_parts)
                resp_headers = {k.lower(): v for k, v in stream_resp.headers.items()}

                return TransportResponse(
                    status_code=stream_resp.status_code,
                    headers=resp_headers,
                    body=resp_body,
                    peer_ip=str(ip_obj),
                    url=request.url,
                )

        except ResponseTooLarge:
            raise
        except httpx.ConnectTimeout as exc:
            raise ConnectTimeout(f"connection timed out: {exc}", code="connect_timeout") from exc
        except httpx.ReadTimeout as exc:
            raise ReadTimeout(f"read timed out: {exc}", code="read_timeout") from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"network timeout: {exc}", code="timeout") from exc
        except httpx.ConnectError as exc:
            raise ConnectError(f"connection failed: {exc}", code="connect_error") from exc
        except httpx.HTTPError as exc:
            raise TransportError(f"transport error: {exc}", code="transport_error") from exc

    def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            self._client.close()
            self._client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
