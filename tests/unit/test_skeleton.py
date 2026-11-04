from __future__ import annotations

import importlib
import json
import socket

import pytest
import pytest_socket

from search_engine import __version__, cli


@pytest.mark.parametrize(
    "name",
    ["api", "crawler", "experience", "extract", "indexer", "net", "operations", "search"],
)
def test_package_imports(name: str) -> None:
    assert importlib.import_module(f"search_engine.{name}").__doc__
    assert __version__


def test_health_command_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["health"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["version"] == __version__


def test_health_has_no_paths() -> None:
    text = json.dumps(cli.health())
    assert "\\" not in text
    assert ":/" not in text


def test_outbound_public_connection_is_blocked() -> None:
    with pytest.raises(pytest_socket.SocketBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


@pytest.mark.loopback
def test_loopback_marker_still_blocks_public_addresses() -> None:
    with pytest.raises(pytest_socket.SocketConnectBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)


@pytest.mark.loopback
def test_loopback_marker_allows_local_listener() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass
