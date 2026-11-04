"""URL normalization (decision D1). Malformed or unsafe URLs cannot produce a NormalizedUrl."""

from __future__ import annotations

import string
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import unquote, urlsplit

import tldextract

from search_engine.crawler.urlsafety import UrlReason, UrlRejected, check_host

MAX_URL_LENGTH = 2048
ALLOWED_PORTS = frozenset({80, 443})
_DEFAULT_PORT = {"http": 80, "https": 443}

_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_HEX_DIGITS = frozenset(string.hexdigits)
_PATH_ALLOWED = frozenset("!$&'()*+,;=:@/")
_QUERY_KEY_ALLOWED = frozenset("!$'()*+,;:@/?")
_QUERY_VALUE_ALLOWED = _QUERY_KEY_ALLOWED | {"="}

_TRACKING_EXACT = frozenset({"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid", "_ga"})
_TRACKING_EXACT_EXTRA = frozenset({"ref_src"})


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    key: str
    scheme: str
    host: str
    port: int | None
    path: str
    query: str
    registered_domain: str

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        port = f":{self.port}" if self.port is not None else ""
        return f"{self.scheme}://{host}{port}"


@lru_cache(maxsize=1)
def _extractor() -> tldextract.TLDExtract:
    # Offline: no suffix-list downloads and no disk cache; uses the snapshot bundled in the package.
    return tldextract.TLDExtract(cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True)


def registered_domain_of(host: str) -> str:
    return str(_extractor()(host).top_domain_under_public_suffix)


def percent_normalize(text: str, allowed: frozenset[str]) -> str:
    """Uppercase escape hex, decode unreserved characters, encode everything illegal."""
    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "%":
            if (
                index + 2 < length
                and text[index + 1] in _HEX_DIGITS
                and text[index + 2] in _HEX_DIGITS
            ):
                value = int(text[index + 1 : index + 3], 16)
                decoded = chr(value)
                out.append(decoded if decoded in _UNRESERVED else f"%{value:02X}")
                index += 3
                continue
            out.append("%25")
        elif char in _UNRESERVED or char in allowed:
            out.append(char)
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode("utf-8"))
        index += 1
    return "".join(out)


def remove_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4."""
    output: list[str] = []
    segments = path.split("/")
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment == ".":
            if last:
                output.append("")
        elif segment == "..":
            if len(output) > 1:
                output.pop()
            if last:
                output.append("")
        else:
            output.append(segment)
    result = "/".join(output)
    return result if result.startswith("/") else "/" + result


def is_tracking_parameter(key: str) -> bool:
    lowered = unquote(key).lower()
    return (
        lowered.startswith("utm_") or lowered in _TRACKING_EXACT or lowered in _TRACKING_EXACT_EXTRA
    )


def _normalize_query(raw: str) -> str:
    params: list[tuple[str, str, str]] = []
    for part in raw.split("&"):
        if not part:
            continue
        key, separator, value = part.partition("=")
        key_n = percent_normalize(key, _QUERY_KEY_ALLOWED)
        if is_tracking_parameter(key_n):
            continue
        value_n = percent_normalize(value, _QUERY_VALUE_ALLOWED)
        params.append((key_n, value_n, separator))
    params.sort()
    return "&".join(f"{key}{sep}{value}" for key, value, sep in params)


def _has_control_chars(text: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F for char in text)


def normalize(url: str, *, allow_loopback: bool = False) -> NormalizedUrl:
    """Return the canonical form of ``url`` or raise UrlRejected with a reason code.

    ``allow_loopback`` is the test-only exception (config ``allow_loopback_for_tests``): it permits
    loopback hosts and any port so fixture servers can be used. It never relaxes anything else.
    """
    text = url.strip()
    if not text:
        raise UrlRejected(UrlReason.EMPTY)
    if len(text) > MAX_URL_LENGTH:
        raise UrlRejected(UrlReason.TOO_LONG)
    if _has_control_chars(text):
        raise UrlRejected(UrlReason.CONTROL_CHARS)
    if "\\" in text:
        raise UrlRejected(UrlReason.MALFORMED, "backslash in URL")

    try:
        parts = urlsplit(text)
        scheme = parts.scheme.lower()
        if scheme not in _DEFAULT_PORT:
            raise UrlRejected(UrlReason.BAD_SCHEME, "only http and https are allowed")
        if "@" in parts.netloc:
            raise UrlRejected(UrlReason.CREDENTIALS, "credentials in URL")
        raw_host = parts.hostname
        port = parts.port
        if raw_host is None:
            raise UrlRejected(UrlReason.BAD_HOST, "missing host")
        info = check_host(
            raw_host, bracketed=parts.netloc.startswith("["), allow_loopback=allow_loopback
        )
        path = (
            remove_dot_segments(percent_normalize(parts.path, _PATH_ALLOWED)) if parts.path else "/"
        )
        query = _normalize_query(parts.query)
    except UrlRejected:
        raise
    except ValueError as exc:  # bad port, bad bracket, lone surrogate
        message = str(exc)
        reason = UrlReason.BAD_PORT if "port" in message.lower() else UrlReason.MALFORMED
        raise UrlRejected(reason, "malformed URL") from exc

    if port is not None and not allow_loopback and port not in ALLOWED_PORTS:
        raise UrlRejected(UrlReason.BAD_PORT, "only ports 80 and 443 are allowed")
    kept_port = None if port is None or port == _DEFAULT_PORT[scheme] else port

    registered = info.host if info.ip is not None else registered_domain_of(info.host)
    if not registered:
        if info.host == "localhost" or info.host.endswith(".localhost"):
            registered = "localhost"
        else:
            raise UrlRejected(UrlReason.NO_REGISTERED_DOMAIN, "host has no registered domain")

    host_text = f"[{info.host}]" if ":" in info.host else info.host
    key = f"{scheme}://{host_text}"
    if kept_port is not None:
        key += f":{kept_port}"
    key += path
    if query:
        key += f"?{query}"
    if len(key) > MAX_URL_LENGTH:
        raise UrlRejected(UrlReason.TOO_LONG)
    return NormalizedUrl(
        key=key,
        scheme=scheme,
        host=info.host,
        port=kept_port,
        path=path,
        query=query,
        registered_domain=registered,
    )


def safety_reason(url: str, *, allow_loopback: bool = False) -> UrlReason | None:
    """String-level safety check: None when the URL is acceptable, else the reason code."""
    try:
        normalize(url, allow_loopback=allow_loopback)
    except UrlRejected as exc:
        return exc.reason
    return None
