from __future__ import annotations

from typing import Any

import pytest
from tests.fixtures.server import running_fixture_server

from search_engine.config import load_settings
from search_engine.crawler.approvals import (
    FetchApproval,
    RobotsFetchApproval,
    issue_fetch_approval,
    issue_robots_approval,
)
from search_engine.crawler.fetcher import (
    FetchResult,
    Redirect,
    SafeFetcher,
    sniff_content,
)
from search_engine.errors import (
    InvalidApproval,
    MimeTypeDisallowed,
    PayloadRejected,
    PolicyDenied,
    RedirectLimitExceeded,
    ResponseTooLargeError,
    StorageLimitExceeded,
)
from search_engine.net.httpx_transport import HttpxTransport

pytestmark = pytest.mark.loopback


# ==============================================================================
# 1. Approval Tests (Forging, Reusing, Sentinel, Type Checks)
# ==============================================================================


def test_approval_forging_prevented() -> None:
    with pytest.raises(InvalidApproval) as exc_info:
        FetchApproval(url="http://example.com/", pinned_ip="127.0.0.1", host="example.com")
    assert exc_info.value.code == "approval_forged"

    with pytest.raises(InvalidApproval) as exc_robots:
        RobotsFetchApproval(
            url="http://example.com/robots.txt", pinned_ip="127.0.0.1", host="example.com"
        )
    assert exc_robots.value.code == "approval_forged"


def test_approval_reuse_rejected() -> None:
    app = issue_fetch_approval("http://example.com/", "127.0.0.1")
    assert not app.is_consumed
    app.consume()
    assert app.is_consumed
    with pytest.raises(InvalidApproval) as exc_info:
        app.consume()
    assert exc_info.value.code == "approval_reused"

    r_app = issue_robots_approval("http://example.com/robots.txt", "127.0.0.1")
    r_app.consume()
    with pytest.raises(InvalidApproval) as exc_robots:
        r_app.consume()
    assert exc_robots.value.code == "approval_reused"


def test_issue_approval_validations() -> None:
    # URL missing hostname
    with pytest.raises(InvalidApproval) as exc_host:
        issue_fetch_approval("/no-host", "127.0.0.1")
    assert exc_host.value.code == "invalid_approval"

    with pytest.raises(InvalidApproval):
        issue_robots_approval("/no-host/robots.txt", "127.0.0.1")

    # Empty pinned_ip
    with pytest.raises(InvalidApproval) as exc_ip:
        issue_fetch_approval("http://example.com/", "")
    assert exc_ip.value.code == "invalid_approval"

    with pytest.raises(InvalidApproval):
        issue_robots_approval("http://example.com/robots.txt", "")


def test_wrong_approval_types_rejected_by_fetcher() -> None:
    settings = load_settings(overrides={"allow_loopback_for_tests": True})
    transport = HttpxTransport(allow_loopback=True)
    fetcher = SafeFetcher(transport, settings)

    r_app = issue_robots_approval("http://example.com/robots.txt", "127.0.0.1")
    with pytest.raises(InvalidApproval) as exc_fetch:
        fetcher.fetch_once(r_app)  # type: ignore[arg-type]
    assert exc_fetch.value.code == "wrong_approval_type"

    f_app = issue_fetch_approval("http://example.com/", "127.0.0.1")
    with pytest.raises(InvalidApproval) as exc_robots:
        fetcher.fetch_robots(f_app)  # type: ignore[arg-type]
    assert exc_robots.value.code == "wrong_approval_type"


def test_host_mismatch_rejected() -> None:
    settings = load_settings(overrides={"allow_loopback_for_tests": True})
    transport = HttpxTransport(allow_loopback=True)
    fetcher = SafeFetcher(transport, settings)

    app = issue_fetch_approval("http://example.com/", "127.0.0.1", host="mismatch.com")
    with pytest.raises(InvalidApproval) as exc_info:
        fetcher.fetch_once(app)
    assert exc_info.value.code == "host_mismatch"


# ==============================================================================
# 2. Fetcher Success, Redirects, and robots.txt
# ==============================================================================


def test_fetch_once_success() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(
            overrides={"allow_loopback_for_tests": True, "policy_url": "https://example.com/policy"}
        )
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/normal"), "127.0.0.1")
        res = fetcher.fetch_once(app)
        assert isinstance(res, FetchResult)
        assert res.status_code == 200
        assert res.outcome == "success"
        assert res.content_type == "text/html"
        assert b"Test Page" in res.body
        assert res.duration_seconds >= 0


def test_fetch_robots_success() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_robots_approval(server_info.url_for("/robots.txt"), "127.0.0.1")
        res = fetcher.fetch_robots(app)
        assert isinstance(res, FetchResult)
        assert res.status_code == 200
        assert res.outcome == "success"
        assert b"User-agent" in res.body


def test_redirect_returned_not_followed() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/redirect/to?target=/normal"), "127.0.0.1")
        res = fetcher.fetch_once(app)
        assert isinstance(res, Redirect)
        assert res.status_code == 302
        assert res.target == server_info.url_for("/normal")


def test_follow_helper_chains_redirects() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        def approve_cb(url: str, hop: int) -> FetchApproval:
            return issue_fetch_approval(url, "127.0.0.1")

        start = server_info.url_for("/redirect/chain?n=2")
        final_res = fetcher.follow(approve_cb, start, max_redirects=5)
        assert isinstance(final_res, FetchResult)
        assert final_res.status_code == 200
        assert b"Test Page" in final_res.body


def test_follow_redirect_loop_stops() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        def approve_cb(url: str, hop: int) -> FetchApproval:
            return issue_fetch_approval(url, "127.0.0.1")

        start = server_info.url_for("/redirect/loop-a")
        with pytest.raises(RedirectLimitExceeded) as exc_info:
            fetcher.follow(approve_cb, start, max_redirects=3)
        assert exc_info.value.code == "too_many_redirects"


def test_follow_unsafe_target_rejected_by_callback() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        def approve_cb(url: str, hop: int) -> FetchApproval:
            if hop > 0:
                raise PolicyDenied("redirect to forbidden target denied", code="policy_denied")
            return issue_fetch_approval(url, "127.0.0.1")

        start = server_info.url_for("/redirect/to?target=http://forbidden.example/evil")
        with pytest.raises(PolicyDenied):
            fetcher.follow(approve_cb, start, max_redirects=5)


# ==============================================================================
# 3. Size Limits, Bombs, and Storage
# ==============================================================================


def test_oversized_body_raises() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(
            server_info.url_for("/huge?size=100000"),
            "127.0.0.1",
            max_bytes=10000,
        )
        with pytest.raises(ResponseTooLargeError) as exc_info:
            fetcher.fetch_once(app)
        assert exc_info.value.code == "response_too_large"


def test_gzip_bomb_stopped_by_decoded_size() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        # 10 MB uncompressed, limit is 100 KB
        app = issue_fetch_approval(
            server_info.url_for("/gzip-bomb?size=10485760"),
            "127.0.0.1",
            max_bytes=100 * 1024,
        )
        with pytest.raises(ResponseTooLargeError) as exc_info:
            fetcher.fetch_once(app)
        assert exc_info.value.code == "response_too_large"


def test_storage_check_limit_exceeded() -> None:
    settings = load_settings(overrides={"allow_loopback_for_tests": True})
    transport = HttpxTransport(allow_loopback=True)
    fetcher = SafeFetcher(transport, settings, storage_check=lambda: False)

    app = issue_fetch_approval("http://example.com/page", "127.0.0.1")
    with pytest.raises(StorageLimitExceeded) as exc_info:
        fetcher.fetch_once(app)
    assert exc_info.value.code == "storage_limit_exceeded"


# ==============================================================================
# 4. Content Type Allowlist & Magic Byte Sniffing
# ==============================================================================


def test_content_type_allowlist_enforced() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/wrong-type"), "127.0.0.1")
        with pytest.raises(MimeTypeDisallowed) as exc_info:
            fetcher.fetch_once(app)
        assert exc_info.value.code == "mime_type_disallowed"


def test_executable_disguised_as_html_rejected() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/executable-as-html"), "127.0.0.1")
        with pytest.raises(PayloadRejected) as exc_info:
            fetcher.fetch_once(app)
        assert exc_info.value.code == "executable_rejected"


def test_svg_image_rejected() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(
            server_info.url_for("/images/vector.svg"), "127.0.0.1", kind="image"
        )
        with pytest.raises((MimeTypeDisallowed, PayloadRejected)) as exc_info:
            fetcher.fetch_once(app)
        assert exc_info.value.code in ("svg_rejected", "mime_type_disallowed")


def test_animated_gif_rejected() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(
            server_info.url_for("/images/animated.gif"), "127.0.0.1", kind="image"
        )
        with pytest.raises((MimeTypeDisallowed, PayloadRejected)):
            fetcher.fetch_once(app)


def test_valid_images_accepted() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        for path, ct in (
            ("/images/valid.jpg", "image/jpeg"),
            ("/images/valid.png", "image/png"),
            ("/images/valid.webp", "image/webp"),
        ):
            app = issue_fetch_approval(server_info.url_for(path), "127.0.0.1", kind="image")
            res = fetcher.fetch_once(app)
            assert isinstance(res, FetchResult)
            assert res.status_code == 200
            assert res.content_type == ct
            assert len(res.body) > 0


def test_sniff_content_mislabeled_and_archives() -> None:
    # PDF not starting with %PDF-
    with pytest.raises(PayloadRejected) as exc_pdf:
        sniff_content(b"not a pdf", "application/pdf")
    assert exc_pdf.value.code == "mislabeled_binary"

    # ZIP disguised as HTML
    with pytest.raises(PayloadRejected) as exc_zip:
        sniff_content(b"PK\x03\x04restofzip", "text/html")
    assert exc_zip.value.code == "archive_rejected"

    # RAR disguised as HTML
    with pytest.raises(PayloadRejected) as exc_rar:
        sniff_content(b"Rar!\x1a\x07rest", "text/html")
    assert exc_rar.value.code == "archive_rejected"

    # 7-Zip disguised as HTML
    with pytest.raises(PayloadRejected) as exc_7z:
        sniff_content(b"7z\xbc\xaf\x27\x1crest", "text/html")
    assert exc_7z.value.code == "archive_rejected"

    # Linux ELF
    with pytest.raises(PayloadRejected) as exc_elf:
        sniff_content(b"\x7fELFbinary", "text/html")
    assert exc_elf.value.code == "executable_rejected"

    # Mach-O
    with pytest.raises(PayloadRejected) as exc_macho:
        sniff_content(b"\xfe\xed\xfa\xcebinary", "text/html")
    assert exc_macho.value.code == "executable_rejected"

    # Mislabeled JPEG, PNG, WebP
    with pytest.raises(PayloadRejected):
        sniff_content(b"not a jpeg", "image/jpeg")
    with pytest.raises(PayloadRejected):
        sniff_content(b"not a png", "image/png")
    with pytest.raises(PayloadRejected):
        sniff_content(b"not a webp", "image/webp")


def test_fetcher_status_408_425_and_invalid_retry_after() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        # Custom handler returning 408
        server_info.server.register_handler(
            "/custom-408", lambda h: h._send_response_simple(408, b"timeout", "text/plain")
        )
        app_408 = issue_fetch_approval(server_info.url_for("/custom-408"), "127.0.0.1")
        res_408 = fetcher.fetch_once(app_408)
        assert isinstance(res_408, FetchResult)
        assert res_408.status_code == 408
        assert res_408.outcome == "retryable"

        # Custom handler returning 429 with invalid Retry-After header
        def h429(h: Any) -> None:
            h.send_response(429)
            h.send_header("Content-Type", "text/plain")
            h.send_header("Retry-After", "invalid-date")
            h.send_header("Content-Length", "2")
            h.end_headers()
            h.wfile.write(b"ok")

        server_info.server.register_handler("/custom-429", h429)
        app_429 = issue_fetch_approval(server_info.url_for("/custom-429"), "127.0.0.1")
        res_429 = fetcher.fetch_once(app_429)
        assert isinstance(res_429, FetchResult)
        assert res_429.status_code == 429
        assert res_429.retry_after_seconds is None


def test_fetch_robots_redirect_and_errors() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        # Robots redirect
        server_info.server.register_handler(
            "/robots.txt", lambda h: h._send_redirect("/robots-target.txt")
        )
        r_app = issue_robots_approval(server_info.url_for("/robots.txt"), "127.0.0.1")
        res = fetcher.fetch_robots(r_app)
        assert isinstance(res, Redirect)
        assert res.target == server_info.url_for("/robots-target.txt")


# ==============================================================================
# 5. Status Codes, 429 Retry-After, Timeouts & Dropped Connections
# ==============================================================================


def test_status_429_retry_after() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(
            overrides={"allow_loopback_for_tests": True, "max_crawl_delay_seconds": 30.0}
        )
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/status/429"), "127.0.0.1")
        res = fetcher.fetch_once(app)
        assert isinstance(res, FetchResult)
        assert res.status_code == 429
        assert res.outcome == "retryable"
        assert res.retry_after_seconds == 5.0


def test_status_500_and_404_classification() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        # 500 is retryable
        app_500 = issue_fetch_approval(server_info.url_for("/status/500"), "127.0.0.1")
        res_500 = fetcher.fetch_once(app_500)
        assert isinstance(res_500, FetchResult)
        assert res_500.status_code == 500
        assert res_500.outcome == "retryable"

        # 404 is permanent
        app_404 = issue_fetch_approval(server_info.url_for("/status/404"), "127.0.0.1")
        res_404 = fetcher.fetch_once(app_404)
        assert isinstance(res_404, FetchResult)
        assert res_404.status_code == 404
        assert res_404.outcome == "permanent"


def test_dropped_connection_is_retryable() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(overrides={"allow_loopback_for_tests": True})
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/drop"), "127.0.0.1")
        res = fetcher.fetch_once(app)
        assert isinstance(res, FetchResult)
        assert res.status_code == 0
        assert res.outcome == "retryable"


def test_read_timeout_is_retryable() -> None:
    with running_fixture_server() as server_info:
        settings = load_settings(
            overrides={"allow_loopback_for_tests": True, "read_timeout_seconds": 0.1}
        )
        transport = HttpxTransport(allow_loopback=True)
        fetcher = SafeFetcher(transport, settings)

        app = issue_fetch_approval(server_info.url_for("/slow?delay=0.5"), "127.0.0.1")
        res = fetcher.fetch_once(app)
        assert isinstance(res, FetchResult)
        assert res.status_code == 0
        assert res.outcome == "retryable"


# ==============================================================================
# 6. Architecture & Security Invariant Tests
# ==============================================================================


def test_fetcher_module_does_not_import_httpx() -> None:
    import ast
    from pathlib import Path

    fetcher_path = Path("src/search_engine/crawler/fetcher.py")
    tree = ast.parse(fetcher_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    all_imports = imported | imported_from
    assert "httpx" not in all_imports
    assert not any(m.startswith("httpx") for m in all_imports)
