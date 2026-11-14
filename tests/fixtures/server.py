"""Scriptable HTTP/HTTPS fixture server for crawler, transport, and safety testing."""

from __future__ import annotations

import gzip
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import trustme

VALID_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

VALID_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06"
    b"\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14"
    b"\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b"
    b"\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01"
    b"\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00"
    b"\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)

VALID_WEBP = (
    b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x00\x30\x01\x00\x9d\x01\x2a\x01\x00\x01\x00\x02"
    b"\x00\x34\x25\xa4\x00"
)

ANIMATED_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x00\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00!\xf9\x04\x00\x00\x00\x00\x00,"
    b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

SVG_IMAGE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="red"/></svg>'
)

# PNG header declaring 50,000 x 50,000 dimensions (decompression bomb)
DECOMPRESSION_BOMB_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\xc3\x50\x00\x00\xc3\x50\x08\x06\x00\x00\x00"
    b"\x37\xa7\xbf\x8d\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FixtureServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 1. Custom registered route handlers
        if path in self.server.custom_handlers:
            handler = self.server.custom_handlers[path]
            handler(self)
            return

        # 2. Dropped connection
        if path == "/drop":
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            except OSError:
                pass
            return

        # 3. Slow response
        if path == "/slow":
            delay = float(query.get("delay", ["1.0"])[0])
            time.sleep(delay)
            self._send_bytes(b"slow ok", "text/plain")
            return

        # 4. Normal HTML page
        if path in ("/", "/normal", "/normal.html"):
            body = (
                b"<!DOCTYPE html><html><head><title>Test Page</title></head>"
                b"<body><h1>Heading</h1><p>Normal paragraph text for extraction.</p></body></html>"
            )
            self._send_bytes(body, "text/html; charset=utf-8")
            return

        # 5. Robots variants
        if path == "/robots.txt":
            content = self.server.robots_content.encode("utf-8")
            self._send_bytes(content, "text/plain")
            return
        if path == "/robots/allow-all":
            self._send_bytes(b"User-agent: *\nAllow: /\n", "text/plain")
            return
        if path == "/robots/disallow-all":
            self._send_bytes(b"User-agent: *\nDisallow: /\n", "text/plain")
            return
        if path == "/robots/crawl-delay":
            self._send_bytes(b"User-agent: *\nCrawl-delay: 5\nAllow: /\n", "text/plain")
            return
        if path == "/robots/huge-delay":
            self._send_bytes(b"User-agent: *\nCrawl-delay: 60\nAllow: /\n", "text/plain")
            return
        if path == "/robots/404":
            self._send_response_simple(404, b"robots not found", "text/plain")
            return
        if path == "/robots/500":
            self._send_response_simple(500, b"robots error", "text/plain")
            return

        # 6. Redirect chains and loops
        if path == "/redirect/to":
            target = query.get("target", ["/normal"])[0]
            self._send_redirect(target)
            return
        if path == "/redirect/chain":
            n = int(query.get("n", ["1"])[0])
            if n <= 1:
                self._send_redirect("/normal")
            else:
                self._send_redirect(f"/redirect/chain?n={n - 1}")
            return
        if path == "/redirect/loop-a":
            self._send_redirect("/redirect/loop-b")
            return
        if path == "/redirect/loop-b":
            self._send_redirect("/redirect/loop-a")
            return

        # 7. Huge content
        if path == "/huge":
            size = int(query.get("size", [str(3 * 1024 * 1024)])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            chunk = b"X" * 65536
            remaining = size
            while remaining > 0:
                to_write = min(remaining, len(chunk))
                self.wfile.write(chunk[:to_write])
                remaining -= to_write
            return

        # 8. Gzip bomb
        if path == "/gzip-bomb":
            decompressed_size = int(query.get("size", [str(10 * 1024 * 1024)])[0])
            data = gzip.compress(b"0" * decompressed_size, mtime=0)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # 9. Wrong content type / binary disguise
        if path == "/wrong-type":
            self._send_bytes(
                b"MZ\x90\x00executable disguised as binary", "application/octet-stream"
            )
            return
        if path == "/executable-as-html":
            self._send_bytes(b"MZ\x90\x00executable binary with html content-type", "text/html")
            return

        # 10. Status codes
        if path == "/status/404":
            self._send_response_simple(404, b"Not Found", "text/plain")
            return
        if path == "/status/429":
            self.send_response(429)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Retry-After", "5")
            self.send_header("Content-Length", "18")
            self.end_headers()
            self.wfile.write(b"Too Many Requests\n")
            return
        if path == "/status/500":
            self._send_response_simple(500, b"Internal Server Error", "text/plain")
            return

        # 11. Feed with image enclosures
        if path == "/feed.xml":
            feed_xml = (
                b'<?xml version="1.0" encoding="utf-8"?>\n'
                b'<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">\n'
                b"<channel>\n"
                b"  <title>Test Feed</title>\n"
                b"  <link>http://example.com/</link>\n"
                b"  <item>\n"
                b"    <title>Article One</title>\n"
                b"    <link>http://example.com/article-1</link>\n"
                b"    <pubDate>Mon, 01 Jan 2026 12:00:00 GMT</pubDate>\n"
                b'    <enclosure url="http://example.com/images/valid.jpg" '
                b'type="image/jpeg" length="123"/>\n'
                b'    <media:thumbnail url="http://example.com/images/valid.png" '
                b'width="160" height="160"/>\n'
                b"  </item>\n"
                b"</channel>\n"
                b"</rss>"
            )
            self._send_bytes(feed_xml, "application/rss+xml; charset=utf-8")
            return

        # 12. Images
        if path == "/images/valid.jpg":
            self._send_bytes(VALID_JPEG, "image/jpeg")
            return
        if path == "/images/valid.png":
            self._send_bytes(VALID_PNG, "image/png")
            return
        if path == "/images/valid.webp":
            self._send_bytes(VALID_WEBP, "image/webp")
            return
        if path == "/images/oversized.jpg":
            # Valid JPEG padded beyond 2 MiB (e.g. 2.5 MiB)
            padded = VALID_JPEG + (b"\x00" * (int(2.5 * 1024 * 1024)))
            self._send_bytes(padded, "image/jpeg")
            return
        if path == "/images/decompression-bomb.png":
            self._send_bytes(DECOMPRESSION_BOMB_PNG, "image/png")
            return
        if path == "/images/vector.svg":
            self._send_bytes(SVG_IMAGE, "image/svg+xml")
            return
        if path == "/images/animated.gif":
            self._send_bytes(ANIMATED_GIF, "image/gif")
            return

        # Default fallback: 404
        self._send_response_simple(404, b"Not Found", "text/plain")

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def _send_response_simple(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_redirect(self, target: str, code: int = 302) -> None:
        self.send_response(code)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, FixtureHandler)
        self.robots_content: str = "User-agent: *\nAllow: /\n"
        self.custom_handlers: dict[str, Callable[[FixtureHandler], None]] = {}

    def register_handler(self, path: str, handler: Callable[[FixtureHandler], None]) -> None:
        self.custom_handlers[path] = handler


class FixtureServer6(FixtureServer):
    address_family = socket.AF_INET6


@dataclass(frozen=True, slots=True)
class FixtureServerInfo:
    server: FixtureServer
    host: str
    port: int
    scheme: str

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def url_for(self, path: str) -> str:
        p = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}{p}"


@contextmanager
def running_fixture_server(
    *,
    ca: trustme.CA | None = None,
    names: list[str] | None = None,
    ipv6: bool = False,
) -> Iterator[FixtureServerInfo]:
    """Context manager spinning up a background fixture server on loopback."""
    bind_ip = "::1" if ipv6 else "127.0.0.1"
    cls = FixtureServer6 if ipv6 else FixtureServer
    server = cls((bind_ip, 0))
    port = int(server.server_address[1])

    scheme = "http"
    if ca is not None:
        scheme = "https"
        cert_names = names or ["localhost", "127.0.0.1", "example.com"]
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ca.issue_cert(*cert_names).configure_cert(context)
        server.socket = context.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host_repr = f"[{bind_ip}]" if ipv6 else bind_ip
    info = FixtureServerInfo(server=server, host=host_repr, port=port, scheme=scheme)
    try:
        yield info
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
