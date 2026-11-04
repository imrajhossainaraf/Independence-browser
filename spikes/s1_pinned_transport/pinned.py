"""Spike S1 prototype: connect to a validated IP while verifying TLS against the hostname.

Technique: send the request to ``https://<ip>:<port>/`` with an explicit ``Host`` header and
the httpx/httpcore ``sni_hostname`` request extension. httpcore passes that value to
``ssl.wrap_socket(server_hostname=...)``, so the certificate is checked against the original
hostname while the TCP connection goes to the pinned address.

Spike code only: the real implementation is re-written with tests in Step 5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

import httpx


class Resolver(Protocol):
    def resolve(self, host: str) -> list[str]: ...


@dataclass
class FakeResolver:
    table: dict[str, list[str]]
    calls: list[str] = field(default_factory=list)

    def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        return self.table[host]


def _format_ip(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip


def pinned_get(client: httpx.Client, url: str, resolver: Resolver) -> httpx.Response:
    """One GET without redirects, pinned to the first resolved address."""
    parts = urlsplit(url)
    host = parts.hostname
    if host is None or parts.scheme != "https":
        raise ValueError("https URL with a host required")
    ip = resolver.resolve(host)[0]
    port = parts.port or 443
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    host_header = host if parts.port in (None, 443) else f"{host}:{parts.port}"
    return client.request(
        "GET",
        f"https://{_format_ip(ip)}:{port}{path}",
        headers={"Host": host_header},
        extensions={"sni_hostname": host},
        follow_redirects=False,
    )


def pinned_fetch(
    client: httpx.Client, url: str, resolver: Resolver, max_hops: int = 5
) -> httpx.Response:
    """Follow redirects manually so every hop is re-resolved and re-pinned (rule S4)."""
    current = url
    for _ in range(max_hops + 1):
        response = pinned_get(client, current, resolver)
        if response.is_redirect and "location" in response.headers:
            current = str(httpx.URL(current).join(response.headers["location"]))
            continue
        return response
    raise RuntimeError("too many redirects")
