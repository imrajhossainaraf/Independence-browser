"""Command line entry point. Only this module may wire components together."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence

from search_engine import __version__


def health() -> dict[str, str]:
    """Return basic, non-sensitive status information (no paths, no secrets)."""
    return {
        "status": "ok",
        "version": __version__,
        "python": platform.python_version(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="search-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="print basic status as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "health":
        print(json.dumps(health()))
        return 0
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":
    sys.exit(main())
