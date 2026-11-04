"""Local HTTPS fixture server for spike S1 (certificates from trustme, loopback only)."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import trustme


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: TlsServer

    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.server.redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/slow":
            time.sleep(1.0)
        body = json.dumps(
            {"host": self.headers.get("Host"), "client_port": self.client_address[1]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


class TlsServer(ThreadingHTTPServer):
    daemon_threads = True
    redirect_target = ""


class TlsServer6(TlsServer):
    address_family = socket.AF_INET6


@contextmanager
def running_server(
    ca: trustme.CA, names: list[str], *, ipv6: bool = False
) -> Iterator[tuple[TlsServer, int]]:
    """Serve HTTPS on loopback with a certificate valid for ``names``."""
    cls = TlsServer6 if ipv6 else TlsServer
    server = cls(("::1" if ipv6 else "127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert(*names).configure_cert(context)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
