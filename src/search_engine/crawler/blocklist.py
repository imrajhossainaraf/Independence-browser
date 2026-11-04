"""Blocklist for hosts, registered domains, and URL prefixes (operator controls build on this)."""

from __future__ import annotations

from dataclasses import dataclass, field

from search_engine.crawler.normalize import NormalizedUrl, normalize


@dataclass
class Blocklist:
    hosts: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)
    prefixes: set[str] = field(default_factory=set)

    def block_host(self, host: str) -> None:
        self.hosts.add(host.strip().lower().rstrip("."))

    def block_domain(self, domain: str) -> None:
        self.domains.add(domain.strip().lower().rstrip("."))

    def block_prefix(self, url_prefix: str) -> None:
        """Block every URL at or below this prefix (raises UrlRejected if the prefix is invalid)."""
        self.prefixes.add(normalize(url_prefix).key)

    def unblock_host(self, host: str) -> None:
        self.hosts.discard(host.strip().lower().rstrip("."))

    def unblock_domain(self, domain: str) -> None:
        self.domains.discard(domain.strip().lower().rstrip("."))

    def unblock_prefix(self, url_prefix: str) -> None:
        self.prefixes.discard(normalize(url_prefix).key)

    def reason(self, url: NormalizedUrl) -> str | None:
        """Why the URL is blocked (``host``, ``domain`` or ``prefix``), or None."""
        if url.host in self.hosts:
            return "host"
        if url.registered_domain in self.domains:
            return "domain"
        for prefix in self.prefixes:
            if url.key == prefix:
                return "prefix"
            if url.key.startswith(prefix) and (
                prefix.endswith("/") or url.key[len(prefix)] in "/?"
            ):
                return "prefix"
        return None

    def is_blocked(self, url: NormalizedUrl) -> bool:
        return self.reason(url) is not None
