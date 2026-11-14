"""DNS resolution abstraction: injectable Resolver protocol, SystemResolver, and FakeResolver."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from search_engine.net.errors import ResolutionError
from search_engine.net.ip_policy import IPAddress


class Resolver(Protocol):
    """Protocol for resolving hostnames to all A and AAAA IP addresses."""

    def resolve(self, host: str, *, timeout: float | None = None) -> list[IPAddress]: ...


class SystemResolver:
    """Production DNS resolver using getaddrinfo with strict timeouts."""

    def __init__(self, *, default_timeout: float = 5.0) -> None:
        self.default_timeout = default_timeout

    def _resolve_raw(self, host: str) -> list[IPAddress]:
        try:
            entries = socket.getaddrinfo(
                host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )
        except socket.gaierror as exc:
            raise ResolutionError(
                f"DNS lookup failed for {host}: {exc}", code="dns_failed"
            ) from exc
        except OSError as exc:
            raise ResolutionError(
                f"network error resolving {host}: {exc}", code="dns_failed"
            ) from exc

        seen: set[IPAddress] = set()
        results: list[IPAddress] = []
        for entry in entries:
            sockaddr = entry[4]
            ip_str = sockaddr[0]
            try:
                addr = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if addr not in seen:
                seen.add(addr)
                results.append(addr)
        if not results:
            raise ResolutionError(f"no A or AAAA records found for {host}", code="dns_no_records")
        return results

    def resolve(self, host: str, *, timeout: float | None = None) -> list[IPAddress]:
        effective_timeout = timeout if timeout is not None else self.default_timeout
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._resolve_raw, host)
            return future.result(timeout=effective_timeout)
        except concurrent.futures.TimeoutError as exc:
            raise ResolutionError(
                f"DNS resolution timed out after {effective_timeout}s for {host}",
                code="dns_timeout",
            ) from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


FakeTableEntry = (
    Sequence[IPAddress | str]
    | Sequence[Sequence[IPAddress | str]]
    | Exception
    | Callable[[], Sequence[IPAddress | str]]
)


@dataclass
class FakeResolver:
    """Configurable resolver for unit tests and DNS rebinding simulation."""

    table: dict[str, FakeTableEntry] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    _call_counts: dict[str, int] = field(default_factory=dict)

    def _coerce_ip(self, val: IPAddress | str) -> IPAddress:
        if isinstance(val, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            return val
        return ipaddress.ip_address(val)

    def resolve(self, host: str, *, timeout: float | None = None) -> list[IPAddress]:
        self.calls.append(host)
        count = self._call_counts.get(host, 0)
        self._call_counts[host] = count + 1

        if host not in self.table:
            raise ResolutionError(f"host not in fake resolver table: {host}", code="dns_failed")

        entry = self.table[host]
        if isinstance(entry, Exception):
            raise entry
        if callable(entry):
            return [self._coerce_ip(x) for x in entry()]

        # Check if entry is a sequence of sequences (sequential answers for DNS rebinding tests)
        if (
            isinstance(entry, (list, tuple))
            and len(entry) > 0
            and isinstance(entry[0], (list, tuple))
        ):
            seq_list: Sequence[Sequence[IPAddress | str]] = entry
            idx = min(count, len(seq_list) - 1)
            return [self._coerce_ip(x) for x in seq_list[idx]]

        # Standard sequence of IPAddress | str
        flat_seq: Sequence[IPAddress | str] = entry  # type: ignore[assignment]
        return [self._coerce_ip(x) for x in flat_seq]
