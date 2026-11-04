"""Import-graph rules (plan Section 5, I-1, I-13, I-16) enforced by reading the source."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "search_engine"
HTTP_MODULES = ("httpx", "requests", "urllib.request", "socket", "aiohttp", "http.client")
ALLOWED_FOR_NET = ("search_engine.net", "search_engine.config")


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _under(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def violations(files: dict[str, str]) -> list[str]:
    """Map of 'package/module.py' (relative to search_engine) -> source. Returns problems."""
    problems: list[str] = []
    for rel, source in files.items():
        parts = rel.replace("\\", "/").split("/")
        package = parts[0] if len(parts) > 1 else parts[0].removesuffix(".py")
        for module in imported_modules(source):
            if package != "net" and _under(module, HTTP_MODULES):
                problems.append(f"{rel}: only net/ may import {module}")
            if package == "net" and module.startswith("search_engine."):
                if not _under(module, ALLOWED_FOR_NET):
                    problems.append(f"{rel}: net may import only config, not {module}")
            if package in {"search", "experience", "api"} and _under(
                module, ("search_engine.net", "search_engine.crawler")
            ):
                problems.append(f"{rel}: {package} must not import {module}")
            if package != "cli" and _under(module, ("search_engine.cli",)):
                problems.append(f"{rel}: only the CLI wires components ({module})")
    return problems


def _read_tree() -> dict[str, str]:
    return {
        path.relative_to(SRC).as_posix(): path.read_text(encoding="utf-8")
        for path in SRC.rglob("*.py")
    }


def test_real_source_tree_respects_import_graph() -> None:
    assert violations(_read_tree()) == []


def test_checker_detects_httpx_outside_net() -> None:
    assert violations({"crawler/fetcher.py": "import httpx"})


def test_checker_allows_httpx_inside_net() -> None:
    assert violations({"net/httpx_transport.py": "import httpx"}) == []


def test_checker_net_may_not_import_crawler() -> None:
    assert violations({"net/x.py": "from search_engine.crawler import fetcher"})


def test_checker_net_may_import_config() -> None:
    assert violations({"net/x.py": "from search_engine.config import Settings"}) == []


def test_checker_search_experience_api_not_import_net_or_crawler() -> None:
    for package in ("search", "experience", "api"):
        assert violations({f"{package}/a.py": "import search_engine.net"})
        assert violations({f"{package}/a.py": "from search_engine.crawler import x"})


def test_checker_only_cli_wires() -> None:
    assert violations({"search/a.py": "from search_engine import cli"})
    assert violations({"cli.py": "import json"}) == []
