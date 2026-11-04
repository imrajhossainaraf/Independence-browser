"""Command line entry point. Only this module may wire components together."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from search_engine import __version__
from search_engine.config import describe, field_sources, load_settings
from search_engine.errors import ConfigError


def health() -> dict[str, str]:
    """Return basic, non-sensitive status information (no paths, no secrets)."""
    return {
        "status": "ok",
        "version": __version__,
        "python": platform.python_version(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="search-engine")
    parser.add_argument("--config-file", type=Path, default=None, help="TOML configuration file")
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override one setting (highest precedence)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="print basic status as JSON")
    config = subparsers.add_parser("config", help="inspect the effective configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    show = config_sub.add_parser("show", help="print effective values and where they came from")
    show.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _parse_overrides(items: Sequence[str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for item in items:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ConfigError(f"--set expects KEY=VALUE, got: {item!r}")
        overrides[key.strip()] = value
    return overrides


def _config_show(args: argparse.Namespace) -> int:
    overrides = _parse_overrides(args.overrides)
    settings = load_settings(args.config_file, overrides)
    rows = describe(settings, field_sources(args.config_file, overrides))
    if args.json:
        print(json.dumps([{"name": n, "value": v, "source": s} for n, v, s in rows], indent=2))
    else:
        width = max(len(name) for name, _, _ in rows)
        for name, value, source in rows:
            print(f"{name.ljust(width)} = {value}  [{source}]")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "health":
            print(json.dumps(health()))
            return 0
        if args.command == "config" and args.config_command == "show":
            return _config_show(args)
    except ConfigError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 3
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":
    sys.exit(main())
