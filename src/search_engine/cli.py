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
from search_engine.db import Database
from search_engine.errors import ConfigError, PolicyDenied
from search_engine.search import SearchService


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

    search_parser = subparsers.add_parser("search", help="search the document index offline")
    search_parser.add_argument("query", help="search terms or quoted phrase")
    search_parser.add_argument("--limit", type=int, default=10, help="max results to return")
    search_parser.add_argument(
        "--type",
        dest="doc_type",
        choices=["page", "pdf", "text", "news"],
        default=None,
        help="filter by document type",
    )
    search_parser.add_argument("--lang", default=None, help="filter by language code")
    search_parser.add_argument("--json", action="store_true", help="machine-readable JSON output")
    search_parser.add_argument("--db", type=Path, default=None, help="database path")

    demo_parser = subparsers.add_parser("demo", help="run offline vertical slice demo")
    demo_parser.add_argument(
        "--db", type=Path, default=None, help="database path (default: data/demo.db)"
    )

    eval_parser = subparsers.add_parser("eval", help="run evaluation harness offline")
    eval_parser.add_argument(
        "--split",
        choices=["dev", "held_out", "all"],
        default="dev",
        help="Query split to evaluate (default: dev).",
    )
    eval_parser.add_argument(
        "--save-baseline",
        type=Path,
        default=None,
        help="Path to save evaluation output as baseline JSON.",
    )
    eval_parser.add_argument(
        "--check-baseline",
        type=Path,
        default=None,
        help="Path to baseline JSON file for regression detection.",
    )
    eval_parser.add_argument(
        "--tolerance",
        type=float,
        default=0.05,
        help="Allowed metric regression threshold (default: 0.05).",
    )
    eval_parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON report instead of Markdown.",
    )
    eval_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional file path to write the output report.",
    )

    crawl_parser = subparsers.add_parser("crawl", help="run controlled web crawler")
    crawl_parser.add_argument(
        "--mode",
        choices=["fixture", "real"],
        default="fixture",
        help="crawl mode: 'fixture' (loopback) or 'real' (preflight approved)",
    )
    crawl_parser.add_argument(
        "--seeds",
        type=Path,
        default=None,
        help="path to seeds.txt (defaults to data/seeds.txt)",
    )
    crawl_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="maximum number of pages to store",
    )
    crawl_parser.add_argument(
        "--time-limit",
        type=float,
        default=None,
        help="maximum crawl runtime in seconds",
    )
    crawl_parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database path",
    )
    crawl_parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable JSON output",
    )

    subparsers.add_parser(
        "approve-preflight",
        help="authorize real-mode crawling by hashing inputs into preflight.approved.json (D13)",
    )

    news_parser = subparsers.add_parser("news", help="manage news ingestion and feeds")
    news_sub = news_parser.add_subparsers(dest="news_command", required=True)
    poll_parser = news_sub.add_parser("poll", help="poll news feeds")
    poll_parser.add_argument("--sources", type=Path, default=None, help="path to news_sources.txt")
    poll_parser.add_argument("--db", type=Path, default=None, help="database path")
    poll_parser.add_argument(
        "--force", action="store_true", help="force poll all enabled feeds regardless of interval"
    )
    poll_parser.add_argument(
        "--mode", choices=["fixture", "real"], default="fixture", help="run mode"
    )
    poll_parser.add_argument("--json", action="store_true", help="machine-readable JSON output")

    serve_parser = subparsers.add_parser("serve", help="start read-only HTTP API server")
    serve_parser.add_argument("--host", default=None, help="host to bind (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=None, help="port to bind (default: 8000)")
    serve_parser.add_argument("--db", type=Path, default=None, help="database path")
    serve_parser.add_argument(
        "--dev", action="store_true", help="run with interactive docs enabled"
    )

    auth_parser = subparsers.add_parser("auth", help="manage API keys for server access")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)

    create_key = auth_sub.add_parser("create-key", help="generate a new API key")
    create_key.add_argument("--name", default="default", help="human label for this key")
    create_key.add_argument("--db", type=Path, default=None, help="database path")

    list_keys = auth_sub.add_parser("list-keys", help="list active API keys (hashes only)")
    list_keys.add_argument("--db", type=Path, default=None, help="database path")
    list_keys.add_argument("--json", action="store_true", help="output as JSON")

    revoke_key = auth_sub.add_parser("revoke-key", help="revoke an API key by id")
    revoke_key.add_argument("id", help="key id to revoke")
    revoke_key.add_argument("--db", type=Path, default=None, help="database path")

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


def _search(args: argparse.Namespace) -> int:
    overrides = _parse_overrides(args.overrides)
    settings = load_settings(args.config_file, overrides)
    db_path = args.db or settings.db_path
    if not db_path.exists():
        if args.json:
            print(
                json.dumps(
                    {"error": "database_not_found", "message": f"database not found at {db_path}"}
                )
            )
        else:
            print(f"error: database not found at {db_path}", file=sys.stderr)
        return 1

    with Database(db_path) as db, db.reader() as conn:
        service = SearchService(conn)
        response = service.search(
            args.query,
            limit=args.limit,
            doc_type=args.doc_type,
            lang=args.lang,
        )

    if args.json:
        print(json.dumps(response.to_dict(), indent=2))
        return 0

    if response.is_auto_corrected and response.original_query:
        print(
            f"Showing results for: {response.query!r} "
            f"(search instead for: {response.original_query!r})"
        )
    elif response.suggestion:
        print(f"Did you mean: {response.suggestion}?")

    print(
        f"Search results for: {response.normalized_query!r} "
        f"(mode: {response.match_mode}, found: {len(response.results)})"
    )
    if not response.results:
        print("No matching documents found.")
        return 0

    print("-" * 70)
    for i, r in enumerate(response.results, 1):
        print(f"[{i}] {r.title}")
        print(f"    URL:   {r.url}")
        print(f"    Type:  {r.doc_type} | Lang: {r.lang} | Score: {r.score:.3f}")
        if r.snippet_text:
            print(f"    Snip:  {r.snippet_text}")
        elif r.description:
            print(f"    Desc:  {r.description}")
        print()
    return 0


def _demo(args: argparse.Namespace) -> int:
    from search_engine.demo import run_demo

    return run_demo(args.db)


def _eval(args: argparse.Namespace) -> int:
    from search_engine.evaluation.evaluator import EVAL_DIR
    from search_engine.evaluation.run_eval import run_evaluation

    args.queries = EVAL_DIR / "queries.csv"
    args.judgments = EVAL_DIR / "judgments.csv"
    return run_evaluation(args)


def _approve_preflight(args: argparse.Namespace) -> int:
    from search_engine.crawler.preflight import create_preflight_approval

    overrides = _parse_overrides(args.overrides)
    settings = load_settings(args.config_file, overrides)
    out_file = create_preflight_approval(settings)
    print(f"Preflight approval created: {out_file}")
    return 0


def _crawl(args: argparse.Namespace) -> int:
    from search_engine.crawler.pipeline import CrawlPipeline

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)

    # Seeds from arguments or settings
    seed_urls: list[str] = []
    seeds_file = args.seeds or settings.seeds_file
    if seeds_file.exists():
        from search_engine.crawler.sources import parse_seeds, read_lines

        allow_loopback = (args.mode == "fixture") or settings.allow_loopback_for_tests
        res = parse_seeds(read_lines(seeds_file), allow_loopback=allow_loopback)
        seed_urls = [u.key for u in res.entries]

    pipeline = CrawlPipeline(settings, mode=args.mode)
    result = pipeline.run(
        seeds=seed_urls,
        max_pages=args.limit,
        max_time_seconds=args.time_limit,
    )

    if args.json:
        out = {
            "run_id": result.run_id,
            "status": result.status,
            "pages_stored": result.pages_stored,
            "duration_seconds": result.duration_seconds,
            "frontier": result.frontier_stats,
            "report": result.report.to_dict(),
        }
        print(json.dumps(out, indent=2))
    else:
        print(result.report.render())
        print(
            f"Status: {result.status} | Pages stored: {result.pages_stored} | "
            f"Duration: {result.duration_seconds:.1f}s"
        )
    return 0


def _news(args: argparse.Namespace) -> int:
    if args.news_command == "poll":
        from search_engine.crawler.news import NewsPoller

        overrides = _parse_overrides(args.overrides)
        if args.db:
            overrides["db_path"] = str(args.db)
        settings = load_settings(args.config_file, overrides)

        poller = NewsPoller(settings, mode=args.mode)
        report = poller.poll(sources_file=args.sources, force=args.force)

        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.render())

        if report.status == "refused_lock_held":
            return 1
        if report.sources_failed > 0 and report.sources_success == 0 and report.sources_polled > 0:
            return 1
        return 0
    return 2


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from search_engine.api.app import create_app
    from search_engine.api.security import verify_server_startup_safety

    overrides = _parse_overrides(args.overrides)
    if args.host:
        overrides["server_host"] = args.host
    if args.port:
        overrides["server_port"] = str(args.port)
    if args.db:
        overrides["db_path"] = str(args.db)
    if args.dev:
        overrides["development_mode"] = "true"

    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()
    try:
        # Refuse startup on non-loopback bind without an API key per D15
        with db.reader() as conn:
            verify_server_startup_safety(settings, conn)

        app = create_app(db, settings=settings)
        print(
            f"Starting search engine API server on http://{settings.server_host}:{settings.server_port}"
        )
        uvicorn.run(app, host=settings.server_host, port=settings.server_port, log_level="info")
        return 0
    finally:
        db.close()


def _auth(args: argparse.Namespace) -> int:
    import secrets
    from datetime import UTC, datetime

    from search_engine.api.security import generate_api_key, hash_api_key

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()

    try:
        if args.auth_command == "create-key":
            key_id = f"key_{secrets.token_hex(6)}"
            raw_key = generate_api_key()
            k_hash = hash_api_key(raw_key)
            now_iso = datetime.now(UTC).isoformat()
            name = args.name.strip()

            with db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO api_keys (id, name, key_hash, created_at, status)
                    VALUES (?, ?, ?, ?, 'active')
                    """,
                    (key_id, name, k_hash, now_iso),
                )

            print(f"Created API key '{name}' (ID: {key_id})")
            print(f"API Key: {raw_key}")
            print(
                "IMPORTANT: Save this key now. It is stored as a SHA-256 hash "
                "and cannot be recovered."
            )
            return 0

        if args.auth_command == "list-keys":
            with db.reader() as conn:
                rows = conn.execute(
                    "SELECT id, name, created_at, status FROM api_keys ORDER BY created_at DESC"
                ).fetchall()
            if args.json:
                data = [
                    {
                        "id": str(r[0]),
                        "name": str(r[1]),
                        "created_at": str(r[2]),
                        "status": str(r[3]),
                    }
                    for r in rows
                ]
                print(json.dumps(data, indent=2))
            else:
                if not rows:
                    print("No API keys found.")
                else:
                    for r in rows:
                        print(f"{r[0]} | {r[1]} | status={r[3]} | created={r[2]}")
            return 0

        if args.auth_command == "revoke-key":
            now_iso = datetime.now(UTC).isoformat()
            with db.transaction() as conn:
                cur = conn.execute(
                    "UPDATE api_keys SET status = 'revoked', revoked_at = ? WHERE id = ?",
                    (now_iso, args.id),
                )
                if cur.rowcount == 0:
                    print(f"Error: key '{args.id}' not found.", file=sys.stderr)
                    return 1
            print(f"Key '{args.id}' has been revoked.")
            return 0

    finally:
        db.close()
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "health":
            print(json.dumps(health()))
            return 0
        if args.command == "config" and args.config_command == "show":
            return _config_show(args)
        if args.command == "search":
            return _search(args)
        if args.command == "demo":
            return _demo(args)
        if args.command == "eval":
            return _eval(args)
        if args.command == "approve-preflight":
            return _approve_preflight(args)
        if args.command == "crawl":
            return _crawl(args)
        if args.command == "news":
            return _news(args)
        if args.command == "serve":
            return _serve(args)
        if args.command == "auth":
            return _auth(args)
    except ConfigError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 3
    except PolicyDenied as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 4
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":
    sys.exit(main())
