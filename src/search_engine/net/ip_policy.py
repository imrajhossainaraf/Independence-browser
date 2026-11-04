"""IP address policy: what counts as a public address (rule S3, decision D2).

Pure functions, no network access. Also used on resolved addresses in Step 5.
"""

from __future__ import annotations

import ipaddress
import re

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Numeric-looking label: decimal, or 0x-prefixed hex (octal is decimal digits with a leading 0).
_NUMERIC_LABEL = re.compile(r"(?:0[xX][0-9a-fA-F]*|[0-9]+)\Z")
_DECIMAL = re.compile(r"[0-9]+\Z")
_HEX = re.compile(r"0[xX][0-9a-fA-F]+\Z")
_OCTAL = re.compile(r"0[0-7]+\Z")


def _label_to_int(label: str) -> int:
    if _HEX.match(label):
        return int(label[2:], 16)
    if _OCTAL.match(label):
        return int(label, 8)
    if _DECIMAL.match(label) and not (len(label) > 1 and label.startswith("0")):
        return int(label)
    raise ValueError(f"invalid numeric host label: {label!r}")


def parse_ipv4_like(host: str) -> ipaddress.IPv4Address | None:
    """Interpret a host the way inet_aton and browsers do (decimal, octal, hex, short forms).

    Returns None when the host is an ordinary name (its last label is not numeric).
    Raises ValueError when the last label is numeric but the host is not a valid address,
    because such a host is ambiguous and must be rejected.
    """
    labels = host.split(".")
    if not _NUMERIC_LABEL.match(labels[-1]):
        return None
    if len(labels) > 4:
        raise ValueError("too many labels for an IPv4 address")
    numbers = [_label_to_int(label) for label in labels]
    if any(number > 255 for number in numbers[:-1]):
        raise ValueError("IPv4 label out of range")
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        raise ValueError("IPv4 final label out of range")
    value = numbers[-1]
    for position, number in enumerate(numbers[:-1]):
        value += number << (8 * (3 - position))
    return ipaddress.IPv4Address(value)


def parse_ipv6(text: str) -> ipaddress.IPv6Address:
    """Parse the inside of a bracketed IPv6 literal. Zone identifiers are rejected."""
    if "%" in text:
        raise ValueError("IPv6 zone identifiers are not allowed")
    return ipaddress.IPv6Address(text)


def is_public_ip(ip: IPAddress) -> bool:
    """True only for ordinary, globally routable unicast addresses.

    Loopback, private, link-local, multicast, reserved, unspecified, carrier-grade NAT,
    unique-local, documentation ranges, and every IPv6 form that embeds an IPv4 address
    (IPv4-mapped, NAT64, 6to4, Teredo) are not public.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None or ip.sixtofour is not None or ip.teredo is not None:
            return False
        if ip in ipaddress.IPv6Network("64:ff9b::/96") or ip in ipaddress.IPv6Network(
            "64:ff9b:1::/48"
        ):
            return False
    flagged = (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )
    return not flagged


def is_loopback_ip(ip: IPAddress) -> bool:
    return ip.is_loopback
