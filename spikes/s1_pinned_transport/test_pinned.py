from __future__ import annotations

import ssl
from collections.abc import Iterator

import httpx
import pytest
import trustme
from pinned import FakeResolver, pinned_fetch, pinned_get
from tls_server import running_server

pytestmark = pytest.mark.loopback


@pytest.fixture(scope="module")
def ca() -> trustme.CA:
    return trustme.CA()


def make_client(ca: trustme.CA, *, keepalive: bool = True, timeout: float = 5.0) -> httpx.Client:
    context = ssl.create_default_context()
    ca.configure_trust(context)
    limits = httpx.Limits(max_keepalive_connections=None if keepalive else 0)
    return httpx.Client(verify=context, limits=limits, timeout=timeout)


@pytest.fixture
def client(ca: trustme.CA) -> Iterator[httpx.Client]:
    with make_client(ca, keepalive=False) as c:
        yield c


def test_host_header_and_certificate_verified_against_hostname(
    ca: trustme.CA, client: httpx.Client
) -> None:
    with running_server(ca, ["pinned.test"]) as (_, port):
        resolver = FakeResolver({"pinned.test": ["127.0.0.1"]})
        response = pinned_get(client, f"https://pinned.test:{port}/", resolver)
    assert response.status_code == 200
    assert response.json()["host"] == f"pinned.test:{port}"
    assert resolver.calls == ["pinned.test"]


def test_certificate_for_another_name_fails(ca: trustme.CA, client: httpx.Client) -> None:
    with running_server(ca, ["other.test"]) as (_, port):
        resolver = FakeResolver({"pinned.test": ["127.0.0.1"]})
        with pytest.raises(httpx.ConnectError) as info:
            pinned_get(client, f"https://pinned.test:{port}/", resolver)
    assert "mismatch" in str(info.value).lower() or "verify" in str(info.value).lower()


def test_untrusted_ca_fails(client: httpx.Client) -> None:
    rogue = trustme.CA()
    with running_server(rogue, ["pinned.test"]) as (_, port):
        resolver = FakeResolver({"pinned.test": ["127.0.0.1"]})
        with pytest.raises(httpx.ConnectError):
            pinned_get(client, f"https://pinned.test:{port}/", resolver)


def test_redirect_hops_are_re_resolved(ca: trustme.CA, client: httpx.Client) -> None:
    with running_server(ca, ["a.test", "b.test"]) as (server, port):
        server.redirect_target = f"https://b.test:{port}/final"
        resolver = FakeResolver({"a.test": ["127.0.0.1"], "b.test": ["127.0.0.1"]})
        response = pinned_fetch(client, f"https://a.test:{port}/redirect", resolver)
    assert response.json()["host"] == f"b.test:{port}"
    assert resolver.calls == ["a.test", "b.test"]


def test_ipv6_pinning(ca: trustme.CA, client: httpx.Client) -> None:
    try:
        ctx = running_server(ca, ["v6.test"], ipv6=True)
        entered = ctx.__enter__()
    except OSError:
        pytest.skip("IPv6 loopback unavailable")
    try:
        _, port = entered
        resolver = FakeResolver({"v6.test": ["::1"]})
        response = pinned_get(client, f"https://v6.test:{port}/", resolver)
        assert response.json()["host"] == f"v6.test:{port}"
    finally:
        ctx.__exit__(None, None, None)


def test_timeout(ca: trustme.CA) -> None:
    with running_server(ca, ["slow.test"]) as (_, port), make_client(ca, timeout=0.3) as client:
        resolver = FakeResolver({"slow.test": ["127.0.0.1"]})
        with pytest.raises(httpx.TimeoutException):
            pinned_get(client, f"https://slow.test:{port}/slow", resolver)


def test_pooled_connection_reuse_crosses_hosts_and_skips_verification(ca: trustme.CA) -> None:
    """HAZARD: with keep-alive the pool key is the IP, so a second hostname on the same
    IP and port reuses the first connection and its certificate check is skipped."""
    with running_server(ca, ["a.test"]) as (_, port), make_client(ca, keepalive=True) as client:
        resolver = FakeResolver({"a.test": ["127.0.0.1"], "b.test": ["127.0.0.1"]})
        first = pinned_get(client, f"https://a.test:{port}/", resolver)
        second = pinned_get(client, f"https://b.test:{port}/", resolver)
    assert first.json()["client_port"] == second.json()["client_port"]
    assert second.status_code == 200  # b.test has no valid certificate, yet it "worked"


def test_no_keepalive_prevents_cross_host_reuse(ca: trustme.CA) -> None:
    """FIX: keep-alive off means every request opens a fresh, freshly verified connection."""
    with running_server(ca, ["a.test"]) as (_, port), make_client(ca, keepalive=False) as client:
        resolver = FakeResolver({"a.test": ["127.0.0.1"], "b.test": ["127.0.0.1"]})
        assert pinned_get(client, f"https://a.test:{port}/", resolver).status_code == 200
        with pytest.raises(httpx.ConnectError):
            pinned_get(client, f"https://b.test:{port}/", resolver)
