"""Test fixtures. All sockets are blocked by default (pytest-socket, see pyproject addopts).

Tests that need a fixture server on this machine carry ``@pytest.mark.loopback``; that
re-enables sockets but only for 127.0.0.1 and ::1.
"""

from __future__ import annotations

import pytest
import pytest_socket


@pytest.fixture(autouse=True)
def _loopback_only_for_marked_tests(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("loopback") is None:
        return
    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts(["127.0.0.1", "::1"], allow_unix_socket=True)
