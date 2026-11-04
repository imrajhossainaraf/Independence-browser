from __future__ import annotations

import ipaddress

import pytest

from search_engine.net.ip_policy import is_public_ip, parse_ipv4_like, parse_ipv6


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.1", "127.0.0.1"),
        ("1.2.3", "1.2.0.3"),
        ("0", "0.0.0.0"),
    ],
)
def test_ipv4_like_forms_are_decoded(host: str, expected: str) -> None:
    assert str(parse_ipv4_like(host)) == expected


@pytest.mark.parametrize("host", ["example.com", "123.example.com", "a.b.c.d", "x1"])
def test_ordinary_names_are_not_ip_addresses(host: str) -> None:
    assert parse_ipv4_like(host) is None


@pytest.mark.parametrize(
    "host",
    ["1.2.3.4.5", "256.1.1.1", "1.2.3.256", "0x", "08", "09.0.0.1", "4294967296", "example.1"],
)
def test_ambiguous_numeric_hosts_raise(host: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        parse_ipv4_like(host)


def test_ipv6_zone_ids_rejected() -> None:
    with pytest.raises(ValueError, match="zone"):
        parse_ipv6("fe80::1%eth0")
    assert str(parse_ipv6("2606:4700:4700::1111")) == "2606:4700:4700::1111"


@pytest.mark.parametrize("text", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(text: str) -> None:
    assert is_public_ip(ipaddress.ip_address(text))


@pytest.mark.parametrize(
    "text",
    [
        "127.0.0.1",
        "10.1.1.1",
        "172.20.0.1",
        "192.168.0.1",
        "169.254.1.1",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
        "2002:808:808::",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
        "2001:db8::1",
    ],
)
def test_non_public_addresses(text: str) -> None:
    assert not is_public_ip(ipaddress.ip_address(text))
