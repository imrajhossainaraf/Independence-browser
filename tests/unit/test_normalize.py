from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from search_engine.crawler.normalize import (
    MAX_URL_LENGTH,
    normalize,
    percent_normalize,
    remove_dot_segments,
    safety_reason,
)
from search_engine.crawler.urlsafety import UrlReason, UrlRejected

# (input, expected key): one row per D1 rule
NORMALIZATION_ROWS = [
    ("HTTP://Example.COM/", "http://example.com/"),  # lowercase scheme and host
    ("https://example.com", "https://example.com/"),  # empty path becomes /
    ("https://example.com:443/a", "https://example.com/a"),  # default https port removed
    ("http://example.com:80/a", "http://example.com/a"),  # default http port removed
    ("https://example.com:80/a", "https://example.com:80/a"),  # allowed but not the default
    ("https://example.com/a#section", "https://example.com/a"),  # fragment removed
    ("https://example.com/a/./b/../c", "https://example.com/a/c"),  # dot segments
    ("https://example.com/../../a", "https://example.com/a"),  # cannot climb above root
    ("https://example.com/a/b/..", "https://example.com/a/"),
    ("https://example.com/%7Euser", "https://example.com/~user"),  # unreserved decoded
    ("https://example.com/a%2fb", "https://example.com/a%2Fb"),  # escape hex uppercased
    ("https://example.com/%2e%2e/x", "https://example.com/x"),  # encoded dots resolved
    ("https://example.com/a b", "https://example.com/a%20b"),  # illegal char encoded
    ("https://example.com/caf\u00e9", "https://example.com/caf%C3%A9"),  # non-ASCII encoded
    ("https://example.com/100%", "https://example.com/100%25"),  # stray percent
    ("https://b\u00fccher.de/", "https://xn--bcher-kva.de/"),  # punycode host
    ("https://example.com./a", "https://example.com/a"),  # trailing dot removed
    ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),  # params sorted
    ("https://example.com/a?a=2&a=1", "https://example.com/a?a=1&a=2"),  # then by value
    ("https://example.com/a?", "https://example.com/a"),  # empty query dropped
    ("https://example.com/a?&&", "https://example.com/a"),
    ("https://example.com/a?id=5&utm_source=x", "https://example.com/a?id=5"),  # tracking dropped
    ("https://example.com/a?UTM_Medium=x&q=1", "https://example.com/a?q=1"),
    ("https://example.com/a?fbclid=1&gclid=2&msclkid=3", "https://example.com/a"),
    ("https://example.com/a?mc_cid=1&mc_eid=2&igshid=3&_ga=4&ref_src=5", "https://example.com/a"),
    ("https://example.com/a?page=2", "https://example.com/a?page=2"),  # meaningful kept
    ("https://example.com/a?a=", "https://example.com/a?a="),  # empty value kept as written
    ("https://example.com/a?q=%7e", "https://example.com/a?q=~"),  # query escapes normalized
    ("https://example.com/index.html", "https://example.com/index.html"),  # kept as written
    ("https://example.com/dir/", "https://example.com/dir/"),  # trailing slash kept
    ("  https://example.com/a  ", "https://example.com/a"),  # surrounding whitespace
    ("https://93.184.216.34/a", "https://93.184.216.34/a"),  # public IPv4 literal
    ("https://[2606:4700:4700::1111]/", "https://[2606:4700:4700::1111]/"),  # public IPv6
]


@pytest.mark.parametrize(("raw", "expected"), NORMALIZATION_ROWS)
def test_normalization_rows(raw: str, expected: str) -> None:
    assert normalize(raw).key == expected


def test_equivalent_urls_share_a_key_and_different_ones_do_not() -> None:
    variants = [
        "https://Example.com:443/a/b?x=1&y=2#top",
        "HTTPS://example.com/a/./b?y=2&x=1",
        "https://example.com/a/c/../b?utm_campaign=z&x=1&y=2",
    ]
    assert len({normalize(v).key for v in variants}) == 1
    assert (
        normalize("https://example.com/a?id=1").key != normalize("https://example.com/a?id=2").key
    )
    assert normalize("https://example.com/a").key != normalize("https://example.com/a/").key


def test_normalized_fields() -> None:
    url = normalize("https://News.Example.co.uk:443/a/b?x=1")
    assert url.scheme == "https"
    assert url.host == "news.example.co.uk"
    assert url.registered_domain == "example.co.uk"
    assert url.port is None
    assert url.path == "/a/b"
    assert url.query == "x=1"
    assert url.origin == "https://news.example.co.uk"
    assert normalize("https://example.com:80/").origin == "https://example.com:80"
    assert normalize("https://[2606:4700:4700::1111]/").origin == "https://[2606:4700:4700::1111]"


UNSAFE_ROWS = [
    ("", UrlReason.EMPTY),
    ("   ", UrlReason.EMPTY),
    ("https://example.com/" + "a" * MAX_URL_LENGTH, UrlReason.TOO_LONG),
    ("https://example.com/a\nb", UrlReason.CONTROL_CHARS),
    ("https://example.com/a b\x00", UrlReason.CONTROL_CHARS),
    ("https://example.com\\@evil.com/", UrlReason.MALFORMED),
    ("ftp://example.com/", UrlReason.BAD_SCHEME),
    ("javascript:alert(1)", UrlReason.BAD_SCHEME),
    ("file:///etc/passwd", UrlReason.BAD_SCHEME),
    ("example.com/path", UrlReason.BAD_SCHEME),
    ("//example.com/path", UrlReason.BAD_SCHEME),
    ("https://user:pass@example.com/", UrlReason.CREDENTIALS),
    ("https://user@example.com/", UrlReason.CREDENTIALS),
    ("https://example.com:8080/", UrlReason.BAD_PORT),
    ("https://example.com:99999/", UrlReason.BAD_PORT),
    ("https://example.com:abc/", UrlReason.BAD_PORT),
    ("https:///path", UrlReason.BAD_HOST),
    ("https://.example.com/", UrlReason.BAD_HOST),
    ("https://exa_mple.com/", UrlReason.BAD_HOST),
    ("https://-bad.example.com/", UrlReason.BAD_HOST),
    ("https://a..example.com/", UrlReason.BAD_HOST),
    ("https://[::1/", UrlReason.MALFORMED),
    ("https://[fe80::1%25eth0]/", UrlReason.BAD_HOST),
    ("https://[notanip]/", UrlReason.MALFORMED),
    ("https://localhost/", UrlReason.UNSAFE_HOST),
    ("https://LOCALHOST./", UrlReason.UNSAFE_HOST),
    ("https://app.localhost/", UrlReason.UNSAFE_HOST),
    ("https://printer.local/", UrlReason.UNSAFE_HOST),
    ("https://db.internal/", UrlReason.UNSAFE_HOST),
    ("https://intranet/", UrlReason.NO_REGISTERED_DOMAIN),
    ("https://foo.invalidtld/", UrlReason.NO_REGISTERED_DOMAIN),
    ("https://example.123/", UrlReason.BAD_HOST),
    ("https://1.2.3.4.5/", UrlReason.BAD_HOST),
]


@pytest.mark.parametrize(("raw", "reason"), UNSAFE_ROWS)
def test_rejected_urls_carry_a_reason(raw: str, reason: UrlReason) -> None:
    with pytest.raises(UrlRejected) as info:
        normalize(raw)
    assert info.value.reason is reason
    assert info.value.code == f"url_{reason.value}"
    assert safety_reason(raw) is reason


def test_safety_reason_is_none_for_good_urls() -> None:
    assert safety_reason("https://example.com/") is None


# D2: every unsafe address class, including obfuscated forms
UNSAFE_IP_HOSTS = [
    "127.0.0.1",
    "127.1",
    "2130706433",
    "0x7f000001",
    "0177.0.0.1",
    "0x7f.0.0.1",
    "10.0.0.1",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    "169.254.169.254",
    "100.64.0.1",
    "0.0.0.0",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    "192.0.2.1",
    "198.51.100.1",
    "198.18.0.1",
    "[::1]",
    "[::]",
    "[fe80::1]",
    "[fc00::1]",
    "[fd12:3456::1]",
    "[ff02::1]",
    "[2001:db8::1]",
    "[::ffff:127.0.0.1]",
    "[::ffff:7f00:1]",
    "[::ffff:8.8.8.8]",
    "[64:ff9b::7f00:1]",
    "[2002:7f00:1::]",
]


@pytest.mark.parametrize("host", UNSAFE_IP_HOSTS)
def test_non_public_ip_literals_are_rejected(host: str) -> None:
    with pytest.raises(UrlRejected) as info:
        normalize(f"https://{host}/")
    assert info.value.reason is UrlReason.NON_PUBLIC_IP


@pytest.mark.parametrize("host", ["127.0.0.1", "2130706433", "[::1]", "localhost", "app.localhost"])
def test_loopback_exception_is_test_only_and_loopback_only(host: str) -> None:
    url = normalize(f"http://{host}:8123/x", allow_loopback=True)
    assert url.port == 8123
    assert url.registered_domain


@pytest.mark.parametrize("host", ["10.0.0.1", "192.168.0.1", "169.254.169.254", "[fc00::1]"])
def test_loopback_exception_never_allows_other_private_ranges(host: str) -> None:
    with pytest.raises(UrlRejected):
        normalize(f"http://{host}/", allow_loopback=True)
    with pytest.raises(UrlRejected):
        normalize("http://printer.local/", allow_loopback=True)


def test_ports_are_restricted_unless_loopback_exception() -> None:
    with pytest.raises(UrlRejected):
        normalize("https://example.com:8443/")
    assert normalize("https://example.com:8443/", allow_loopback=True).port == 8443


def test_remove_dot_segments_rfc_examples() -> None:
    assert remove_dot_segments("/a/b/c/./../../g") == "/a/g"
    assert remove_dot_segments("/mid/content=5/../6") == "/mid/6"
    assert remove_dot_segments("/./a") == "/a"


def test_percent_normalize_keeps_reserved_escapes() -> None:
    assert percent_normalize("%2f%3F%41", frozenset()) == "%2F%3FA"


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=200))
def test_normalize_never_raises_anything_but_url_rejected(raw: str) -> None:
    try:
        normalize(raw)
    except UrlRejected:
        pass


@settings(max_examples=300, deadline=None)
@given(
    path=st.text(alphabet=st.characters(codec="utf-8"), max_size=40),
    query=st.text(alphabet=st.characters(codec="utf-8"), max_size=40),
)
def test_normalization_is_idempotent(path: str, query: str) -> None:
    try:
        first = normalize(f"https://example.com/{path}?{query}")
    except UrlRejected:
        return
    assert normalize(first.key).key == first.key
