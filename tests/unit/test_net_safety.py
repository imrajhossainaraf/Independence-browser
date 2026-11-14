from __future__ import annotations

import ipaddress
import ssl

import pytest
import trustme
from tests.fixtures.server import (
    ANIMATED_GIF,
    DECOMPRESSION_BOMB_PNG,
    SVG_IMAGE,
    VALID_JPEG,
    VALID_PNG,
    VALID_WEBP,
    running_fixture_server,
)

from search_engine.config import load_settings
from search_engine.errors import ConfigError
from search_engine.net.errors import (
    ConnectError,
    InvalidAddressError,
    ReadTimeout,
    ResolutionError,
    ResponseTooLarge,
    TimeoutError,
    TransportError,
)
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.ip_policy import (
    is_allowed_ip,
    is_public_ip,
    validate_resolved_ips,
)
from search_engine.net.resolver import FakeResolver, SystemResolver
from search_engine.net.transport import FakeTransport, TransportRequest, TransportResponse

pytestmark = pytest.mark.loopback


# ==============================================================================
# 1. IP Policy & Validation Tests
# ==============================================================================


def test_public_ips_allowed() -> None:
    pub4 = ipaddress.ip_address("93.184.216.34")
    pub6 = ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946")
    assert is_public_ip(pub4)
    assert is_public_ip(pub6)
    assert is_allowed_ip(pub4, allow_loopback=False)
    assert is_allowed_ip(pub6, allow_loopback=False)
    assert validate_resolved_ips([pub4, pub6], allow_loopback=False) == [pub4, pub6]


def test_private_ips_rejected() -> None:
    privates = [
        ipaddress.ip_address("10.0.0.1"),
        ipaddress.ip_address("172.16.0.1"),
        ipaddress.ip_address("192.168.1.1"),
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.64.0.1"),  # CGNAT
        ipaddress.ip_address("192.0.2.1"),  # Documentation
        ipaddress.ip_address("224.0.0.1"),  # Multicast
        ipaddress.ip_address("0.0.0.0"),  # Unspecified
    ]
    for ip in privates:
        assert not is_public_ip(ip)
        assert not is_allowed_ip(ip, allow_loopback=False)
        assert not is_allowed_ip(
            ip, allow_loopback=True
        )  # loopback flag does not allow private LAN
        with pytest.raises(InvalidAddressError) as exc_info:
            validate_resolved_ips([ip], allow_loopback=False)
        assert exc_info.value.code == "address_not_allowed"


def test_mixed_records_rejected() -> None:
    pub = ipaddress.ip_address("93.184.216.34")
    priv = ipaddress.ip_address("192.168.1.1")
    with pytest.raises(InvalidAddressError):
        validate_resolved_ips([pub, priv], allow_loopback=False)
    with pytest.raises(InvalidAddressError):
        validate_resolved_ips([priv, pub], allow_loopback=False)


def test_loopback_rejected_without_flag_and_allowed_with_flag() -> None:
    loop4 = ipaddress.ip_address("127.0.0.1")
    loop6 = ipaddress.ip_address("::1")
    assert not is_public_ip(loop4)
    assert not is_public_ip(loop6)

    # Without allow_loopback
    assert not is_allowed_ip(loop4, allow_loopback=False)
    assert not is_allowed_ip(loop6, allow_loopback=False)
    with pytest.raises(InvalidAddressError):
        validate_resolved_ips([loop4], allow_loopback=False)

    # With allow_loopback
    assert is_allowed_ip(loop4, allow_loopback=True)
    assert is_allowed_ip(loop6, allow_loopback=True)
    assert validate_resolved_ips([loop4, loop6], allow_loopback=True) == [loop4, loop6]


def test_ipv6_special_and_mapped_addresses_rejected() -> None:
    bad_v6 = [
        ipaddress.ip_address("::ffff:192.0.2.1"),  # IPv4-mapped
        ipaddress.ip_address("::ffff:127.0.0.1"),  # IPv4-mapped loopback
        ipaddress.ip_address("2002:c000:0201::"),  # 6to4
        ipaddress.ip_address("2001:0000:4136:e378:8000:63bf:3fff:fdd2"),  # Teredo
        ipaddress.ip_address("64:ff9b::192.0.2.1"),  # NAT64
        ipaddress.ip_address("fe80::1"),  # Link-local
        ipaddress.ip_address("fc00::1"),  # Unique local
        ipaddress.ip_address("ff02::1"),  # Multicast
    ]
    for ip in bad_v6:
        assert not is_public_ip(ip)
        assert not is_allowed_ip(ip, allow_loopback=False)
        assert not is_allowed_ip(ip, allow_loopback=True)
        with pytest.raises(InvalidAddressError):
            validate_resolved_ips([ip], allow_loopback=True)


def test_empty_resolved_addresses_rejected() -> None:
    with pytest.raises(InvalidAddressError) as exc_info:
        validate_resolved_ips([])
    assert exc_info.value.code == "no_addresses"


# ==============================================================================
# 2. Configuration & Gate Flags Coexistence
# ==============================================================================


def test_loopback_and_real_network_cannot_coexist() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_settings(overrides={"real_network_enabled": True, "allow_loopback_for_tests": True})
    assert "cannot both be true" in str(exc_info.value).lower()


# ==============================================================================
# 3. Resolver Tests (SystemResolver & FakeResolver)
# ==============================================================================


def test_fake_resolver_static_and_sequential() -> None:
    resolver = FakeResolver(
        table={
            "example.com": ["93.184.216.34"],
            "rebind.test": [["93.184.216.34"], ["192.168.1.1"]],
        }
    )
    # Static resolution
    assert resolver.resolve("example.com") == [ipaddress.ip_address("93.184.216.34")]
    assert resolver.calls == ["example.com"]

    # Sequential DNS rebinding answers
    first_ans = resolver.resolve("rebind.test")
    second_ans = resolver.resolve("rebind.test")
    assert first_ans == [ipaddress.ip_address("93.184.216.34")]
    assert second_ans == [ipaddress.ip_address("192.168.1.1")]


def test_fake_resolver_unknown_host_raises() -> None:
    resolver = FakeResolver()
    with pytest.raises(ResolutionError) as exc_info:
        resolver.resolve("missing.test")
    assert exc_info.value.code == "dns_failed"


def test_fake_resolver_exception_entry() -> None:
    resolver = FakeResolver(table={"boom.test": ResolutionError("DNS exploded", code="dns_failed")})
    with pytest.raises(ResolutionError):
        resolver.resolve("boom.test")


def test_system_resolver_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = SystemResolver(default_timeout=0.01)

    def slow_resolve(host: str) -> list[ipaddress.IPv4Address]:
        import time

        time.sleep(0.5)
        return [ipaddress.IPv4Address("127.0.0.1")]

    monkeypatch.setattr(resolver, "_resolve_raw", slow_resolve)
    with pytest.raises(ResolutionError) as exc_info:
        resolver.resolve("slow.test")
    assert exc_info.value.code == "dns_timeout"


def test_system_resolver_mocked_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = SystemResolver()

    # Success with IPv4 and IPv6 deduplication
    import socket

    def mock_gai_success(host: str, *args: object, **kwargs: object) -> list[object]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),  # duplicate
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("invalid_ip", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mock_gai_success)
    res = resolver.resolve("example.com")
    assert len(res) == 2
    assert res[0] == ipaddress.ip_address("93.184.216.34")
    assert res[1] == ipaddress.ip_address("2606:2800:220:1:248:1893:25c8:1946")

    # Empty results error
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [])
    with pytest.raises(ResolutionError) as exc_empty:
        resolver.resolve("empty.com")
    assert exc_empty.value.code == "dns_no_records"

    # socket.gaierror
    def mock_gai_fail(*a: object, **kw: object) -> list[object]:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", mock_gai_fail)
    with pytest.raises(ResolutionError) as exc_fail:
        resolver.resolve("fail.com")
    assert exc_fail.value.code == "dns_failed"

    # OSError
    def mock_gai_oserror(*a: object, **kw: object) -> list[object]:
        raise OSError("Network unreachable")

    monkeypatch.setattr(socket, "getaddrinfo", mock_gai_oserror)
    with pytest.raises(ResolutionError) as exc_os:
        resolver.resolve("fail.com")
    assert exc_os.value.code == "dns_failed"


# ==============================================================================
# 4. Transport & Security Invariant Tests
# ==============================================================================


@pytest.fixture(scope="module")
def ca() -> trustme.CA:
    return trustme.CA()


def test_transport_refuses_unvalidated_address_before_connecting() -> None:
    transport = HttpxTransport(allow_loopback=False)
    # 10.0.0.1 is private
    req = TransportRequest(
        method="GET",
        url="https://example.com/test",
        pinned_ip="10.0.0.1",
        server_name="example.com",
    )
    with pytest.raises(InvalidAddressError) as exc_info:
        transport.send(req)
    assert exc_info.value.code == "address_not_allowed"

    # 127.0.0.1 is refused when allow_loopback is False
    req_loop = TransportRequest(
        method="GET",
        url="https://example.com/test",
        pinned_ip="127.0.0.1",
        server_name="example.com",
    )
    with pytest.raises(InvalidAddressError) as exc_info:
        transport.send(req_loop)
    assert exc_info.value.code == "address_not_allowed"


def test_httpx_transport_https_pinned_connection(ca: trustme.CA) -> None:
    with running_fixture_server(ca=ca, names=["example.com"]) as server_info:
        context = ssl.create_default_context()
        ca.configure_trust(context)
        transport = HttpxTransport(allow_loopback=True, verify=context)

        req = TransportRequest(
            method="GET",
            url=f"https://example.com:{server_info.port}/normal",
            pinned_ip="127.0.0.1",
            server_name="example.com",
        )
        resp = transport.send(req)
        assert resp.status_code == 200
        assert b"Normal paragraph" in resp.body
        assert resp.peer_ip == "127.0.0.1"


def test_httpx_transport_cert_hostname_mismatch_fails(ca: trustme.CA) -> None:
    with running_fixture_server(ca=ca, names=["legit.com"]) as server_info:
        context = ssl.create_default_context()
        ca.configure_trust(context)
        transport = HttpxTransport(allow_loopback=True, verify=context)

        req = TransportRequest(
            method="GET",
            url=f"https://attacker.com:{server_info.port}/normal",
            pinned_ip="127.0.0.1",
            server_name="attacker.com",
        )
        with pytest.raises(ConnectError):
            transport.send(req)


def test_httpx_transport_untrusted_ca_fails() -> None:
    other_ca = trustme.CA()
    with running_fixture_server(ca=other_ca, names=["example.com"]) as server_info:
        # Default system CA trust will not trust other_ca
        transport = HttpxTransport(allow_loopback=True, verify=True)
        req = TransportRequest(
            method="GET",
            url=f"https://example.com:{server_info.port}/normal",
            pinned_ip="127.0.0.1",
            server_name="example.com",
        )
        with pytest.raises(ConnectError):
            transport.send(req)


def test_httpx_transport_no_keepalive_prevents_connection_reuse(ca: trustme.CA) -> None:
    """Verifies ADR-006: keep-alive is disabled so every request verifies its own TLS handshake."""
    with running_fixture_server(ca=ca, names=["host-a.com"]) as server_info:
        context = ssl.create_default_context()
        ca.configure_trust(context)
        transport = HttpxTransport(allow_loopback=True, verify=context)

        # 1. First request to host-a succeeds
        req1 = TransportRequest(
            method="GET",
            url=f"https://host-a.com:{server_info.port}/normal",
            pinned_ip="127.0.0.1",
            server_name="host-a.com",
        )
        resp1 = transport.send(req1)
        assert resp1.status_code == 200

        # 2. Second request to host-b against same IP must NOT reuse connection and must fail TLS
        req2 = TransportRequest(
            method="GET",
            url=f"https://host-b.com:{server_info.port}/normal",
            pinned_ip="127.0.0.1",
            server_name="host-b.com",
        )
        with pytest.raises(ConnectError):
            transport.send(req2)


def test_httpx_transport_streaming_max_bytes_limit() -> None:
    with running_fixture_server() as server_info:
        transport = HttpxTransport(allow_loopback=True)
        req = TransportRequest(
            method="GET",
            url=f"http://example.com:{server_info.port}/huge?size=100000",
            pinned_ip="127.0.0.1",
            server_name="example.com",
        )
        # Set max_bytes lower than payload
        with pytest.raises(ResponseTooLarge) as exc_info:
            transport.send(req, max_bytes=5000)
        assert exc_info.value.code == "response_too_large"


def test_httpx_transport_read_timeout() -> None:
    with running_fixture_server() as server_info:
        transport = HttpxTransport(allow_loopback=True)
        req = TransportRequest(
            method="GET",
            url=f"http://example.com:{server_info.port}/slow?delay=0.5",
            pinned_ip="127.0.0.1",
            server_name="example.com",
            read_timeout=0.1,
        )
        with pytest.raises((ReadTimeout, TimeoutError)):
            transport.send(req)


# ==============================================================================
# 5. DNS Rebinding Simulation
# ==============================================================================


def test_dns_rebinding_simulation() -> None:
    """Simulate DNS rebinding where a domain first resolves to a valid IP,

    then re-resolves to a private IP on a redirect hop.
    """
    resolver = FakeResolver(
        table={
            "initial.com": [["93.184.216.34"], ["10.0.0.1"]],
            "redirect.com": [["192.168.1.50"]],
        }
    )

    # 1. First resolution passes policy
    addrs1 = resolver.resolve("initial.com")
    validated1 = validate_resolved_ips(addrs1, allow_loopback=False)
    assert validated1 == [ipaddress.ip_address("93.184.216.34")]

    # 2. Next resolution for rebinding returns private address and is rejected
    addrs2 = resolver.resolve("initial.com")
    with pytest.raises(InvalidAddressError) as exc_info:
        validate_resolved_ips(addrs2, allow_loopback=False)
    assert exc_info.value.code == "address_not_allowed"

    # 3. Redirect target resolution to private address is rejected
    addrs_redir = resolver.resolve("redirect.com")
    with pytest.raises(InvalidAddressError):
        validate_resolved_ips(addrs_redir, allow_loopback=False)


# ==============================================================================
# 6. Fixture Server Scriptable Routes Coverage
# ==============================================================================


def test_fixture_server_routes_coverage() -> None:
    with running_fixture_server() as server_info:
        transport = HttpxTransport(allow_loopback=True)

        def get(path: str) -> TransportResponse:
            return transport.send(
                TransportRequest(
                    method="GET",
                    url=server_info.url_for(path),
                    pinned_ip="127.0.0.1",
                    server_name="example.com",
                )
            )

        # Normal page
        resp = get("/normal")
        assert resp.status_code == 200
        assert b"Test Page" in resp.body

        # Robots variants
        assert get("/robots/allow-all").status_code == 200
        assert get("/robots/disallow-all").status_code == 200
        assert get("/robots/crawl-delay").status_code == 200
        assert get("/robots/huge-delay").status_code == 200
        assert get("/robots/404").status_code == 404
        assert get("/robots/500").status_code == 500

        # Redirects (not automatically followed by transport)
        redir = get("/redirect/to?target=/normal")
        assert redir.status_code == 302
        assert redir.headers["location"] == "/normal"

        chain = get("/redirect/chain?n=2")
        assert chain.status_code == 302
        assert chain.headers["location"] == "/redirect/chain?n=1"

        loop_a = get("/redirect/loop-a")
        assert loop_a.status_code == 302
        assert loop_a.headers["location"] == "/redirect/loop-b"

        # Gzip bomb
        bomb = get("/gzip-bomb")
        assert bomb.status_code == 200
        assert bomb.headers.get("content-encoding") == "gzip"

        # Wrong content types
        wrong = get("/wrong-type")
        assert wrong.status_code == 200
        assert wrong.headers.get("content-type") == "application/octet-stream"

        as_html = get("/executable-as-html")
        assert as_html.status_code == 200
        assert as_html.headers.get("content-type") == "text/html"

        # Status codes
        assert get("/status/404").status_code == 404
        r429 = get("/status/429")
        assert r429.status_code == 429
        assert r429.headers.get("retry-after") == "5"
        assert get("/status/500").status_code == 500

        # Feed with image enclosures
        feed = get("/feed.xml")
        assert feed.status_code == 200
        assert b"<enclosure" in feed.body
        assert b"<media:thumbnail" in feed.body

        # Images
        img_jpg = get("/images/valid.jpg")
        assert img_jpg.status_code == 200
        assert img_jpg.body == VALID_JPEG

        img_png = get("/images/valid.png")
        assert img_png.status_code == 200
        assert img_png.body == VALID_PNG

        img_webp = get("/images/valid.webp")
        assert img_webp.status_code == 200
        assert img_webp.body == VALID_WEBP

        img_over = get("/images/oversized.jpg")
        assert img_over.status_code == 200
        assert len(img_over.body) > 2 * 1024 * 1024

        img_bomb = get("/images/decompression-bomb.png")
        assert img_bomb.status_code == 200
        assert img_bomb.body == DECOMPRESSION_BOMB_PNG

        img_svg = get("/images/vector.svg")
        assert img_svg.status_code == 200
        assert img_svg.body == SVG_IMAGE

        img_gif = get("/images/animated.gif")
        assert img_gif.status_code == 200
        assert img_gif.body == ANIMATED_GIF


def test_fixture_server_dropped_connection() -> None:
    with running_fixture_server() as server_info:
        transport = HttpxTransport(allow_loopback=True)
        req = TransportRequest(
            method="GET",
            url=server_info.url_for("/drop"),
            pinned_ip="127.0.0.1",
            server_name="example.com",
        )
        with pytest.raises((TransportError, ConnectError)):
            transport.send(req)


def test_fake_transport() -> None:
    fake = FakeTransport(allow_loopback=True)
    fake.register(
        "http://example.com/test",
        TransportResponse(
            status_code=200,
            headers={"content-type": "text/plain"},
            body=b"ok",
            peer_ip="127.0.0.1",
            url="http://example.com/test",
        ),
    )
    req = TransportRequest(
        method="GET",
        url="http://example.com/test",
        pinned_ip="127.0.0.1",
    )
    resp = fake.send(req)
    assert resp.status_code == 200
    assert resp.body == b"ok"
    assert resp.header("Content-Type") == "text/plain"
    assert len(fake.requests) == 1

    # Fake transport with custom handler
    fake.add_handler(
        lambda r: (
            TransportResponse(204, {}, b"", "127.0.0.1", r.url)
            if r.url.endswith("/custom")
            else None
        )
    )
    custom_req = TransportRequest(
        method="GET", url="http://example.com/custom", pinned_ip="127.0.0.1"
    )
    assert fake.send(custom_req).status_code == 204

    # Fake transport unregistered URL error
    with pytest.raises(TransportError):
        fake.send(
            TransportRequest(method="GET", url="http://example.com/missing", pinned_ip="127.0.0.1")
        )
    fake.close()


def test_httpx_transport_unsupported_scheme() -> None:
    transport = HttpxTransport(allow_loopback=True)
    req = TransportRequest(method="GET", url="ftp://example.com/file", pinned_ip="127.0.0.1")
    with pytest.raises(TransportError) as exc_info:
        transport.send(req)
    assert exc_info.value.code == "unsupported_scheme"


def test_httpx_transport_context_manager() -> None:
    with HttpxTransport(allow_loopback=True) as transport:
        assert transport._client is None
