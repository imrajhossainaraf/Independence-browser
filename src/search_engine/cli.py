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
from search_engine.errors import ConfigError, PolicyDenied, StorageError
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

    dis_src = news_sub.add_parser("disable-source", help="disable a news feed source")
    dis_src.add_argument("source", help="feed URL, key, or display name")
    dis_src.add_argument("--db", type=Path, default=None, help="database path")

    rem_it = news_sub.add_parser("remove-item", help="remove a news item from search and home")
    rem_it.add_argument("id", help="news item id")
    rem_it.add_argument("--reason", default="operator_removal", help="removal reason")
    rem_it.add_argument("--db", type=Path, default=None, help="database path")

    serve_parser = subparsers.add_parser("serve", help="start read-only HTTP API server")
    serve_parser.add_argument("--host", default=None, help="host to bind (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=None, help="port to bind (default: 8000)")
    serve_parser.add_argument("--db", type=Path, default=None, help="database path")
    serve_parser.add_argument(
        "--dev", action="store_true", help="run with interactive docs enabled"
    )
    serve_parser.add_argument(
        "--offline", action="store_true", help="run in strict offline mode with zero network access"
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

    backup_parser = subparsers.add_parser(
        "backup", help="create an online snapshot backup of database, raw files, and images"
    )
    backup_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="backup destination directory (default: backups/backup-<timestamp>)",
    )
    backup_parser.add_argument("--db", type=Path, default=None, help="database path")
    backup_parser.add_argument("--raw-dir", type=Path, default=None, help="raw storage directory")
    backup_parser.add_argument(
        "--image-dir", type=Path, default=None, help="image storage directory"
    )
    backup_parser.add_argument("--json", action="store_true", help="output manifest as JSON")

    restore_parser = subparsers.add_parser(
        "restore", help="restore a verified backup into an empty target directory"
    )
    restore_parser.add_argument(
        "--backup-dir",
        "-b",
        type=Path,
        required=True,
        help="path to backup directory containing manifest.json",
    )
    restore_parser.add_argument(
        "--target-dir",
        "-t",
        type=Path,
        required=True,
        help="empty target directory to restore into",
    )
    restore_parser.add_argument("--json", action="store_true", help="output restore report as JSON")

    # Operator controls (Step 25)
    block_p = subparsers.add_parser("block", help="block a host, domain, or prefix")
    block_p.add_argument("kind", choices=["host", "domain", "prefix"])
    block_p.add_argument("target", help="target host, domain, or prefix to block")
    block_p.add_argument("--reason", default="operator_block", help="reason for block")
    block_p.add_argument("--db", type=Path, default=None, help="database path")

    unblock_p = subparsers.add_parser("unblock", help="unblock a host, domain, or prefix")
    unblock_p.add_argument("kind", choices=["host", "domain", "prefix"])
    unblock_p.add_argument("target", help="target host, domain, or prefix to unblock")
    unblock_p.add_argument("--db", type=Path, default=None, help="database path")

    blocks_p = subparsers.add_parser("blocks", help="list active operator blocks")
    blocks_p.add_argument("--json", action="store_true", help="output as JSON")
    blocks_p.add_argument("--db", type=Path, default=None, help="database path")

    remove_p = subparsers.add_parser("remove", help="remove document by url or domain")
    remove_p.add_argument("--url", default=None, help="exact URL to remove")
    remove_p.add_argument("--domain", default=None, help="registered domain to remove")
    remove_p.add_argument(
        "--purge-raw", action="store_true", help="purge unreferenced raw files from disk"
    )
    remove_p.add_argument("--reason", default="operator_removal", help="removal reason")
    remove_p.add_argument("--db", type=Path, default=None, help="database path")
    remove_p.add_argument("--json", action="store_true", help="output as JSON")

    images_p = subparsers.add_parser("images", help="image operations")
    images_sub = images_p.add_subparsers(dest="images_command", required=True)
    takedown_p = images_sub.add_parser("takedown", help="takedown an image and delete files")
    takedown_p.add_argument("id", help="image id")
    takedown_p.add_argument("--reason", default="operator_takedown", help="reason")
    takedown_p.add_argument("--db", type=Path, default=None, help="database path")

    entities_p = subparsers.add_parser("entities", help="entity operations")
    entities_sub = entities_p.add_subparsers(dest="entities_command", required=True)
    ent_rem = entities_sub.add_parser("remove", help="remove an entity")
    ent_rem.add_argument("id", help="entity id or name")
    ent_rem.add_argument("--db", type=Path, default=None, help="database path")

    pause_p = subparsers.add_parser("pause", help="pause the crawler via PAUSE file")
    pause_p.add_argument("--data-dir", type=Path, default=None, help="data directory")

    resume_p = subparsers.add_parser("resume", help="resume the crawler by removing PAUSE file")
    resume_p.add_argument("--data-dir", type=Path, default=None, help="data directory")

    stop_p = subparsers.add_parser("stop", help="stop the crawler gracefully via STOP file")
    stop_p.add_argument("--data-dir", type=Path, default=None, help="data directory")

    status_p = subparsers.add_parser("status", help="show operational engine and lock status")
    status_p.add_argument("--json", action="store_true", help="output as JSON")
    status_p.add_argument("--db", type=Path, default=None, help="database path")

    retain_p = subparsers.add_parser("retain", help="manage data retention")
    retain_sub = retain_p.add_subparsers(dest="retain_command", required=True)
    purge_p = retain_sub.add_parser("purge", help="enforce retention and prune expired data")
    purge_p.add_argument("--days", type=int, default=14, help="news retention days (default: 14)")
    purge_p.add_argument(
        "--cap", type=int, default=500, help="max active news items (default: 500)"
    )
    purge_p.add_argument("--db", type=Path, default=None, help="database path")
    purge_p.add_argument("--json", action="store_true", help="output as JSON")

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

    if args.news_command == "disable-source":
        from search_engine.operations.controls import disable_news_source

        overrides = _parse_overrides(args.overrides)
        if args.db:
            overrides["db_path"] = str(args.db)
        settings = load_settings(args.config_file, overrides)
        db = Database(Path(settings.db_path)).open()
        try:
            ok = disable_news_source(db, args.source)
            if ok:
                print(f"News source '{args.source}' disabled and items deactivated.")
                return 0
            print(f"News source '{args.source}' not found.", file=sys.stderr)
            return 1
        finally:
            db.close()

    if args.news_command == "remove-item":
        from search_engine.operations.controls import remove_news_item

        overrides = _parse_overrides(args.overrides)
        if args.db:
            overrides["db_path"] = str(args.db)
        settings = load_settings(args.config_file, overrides)
        db = Database(Path(settings.db_path)).open()
        try:
            ok = remove_news_item(db, args.id, reason=args.reason)
            if ok:
                print(f"News item '{args.id}' removed.")
                return 0
            print(f"News item '{args.id}' not found.", file=sys.stderr)
            return 1
        finally:
            db.close()

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
    if args.offline:
        overrides["offline_mode"] = "true"

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


def _backup(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from search_engine.operations.backup import create_backup

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    if args.raw_dir:
        overrides["raw_dir"] = str(args.raw_dir)
    if args.image_dir:
        overrides["image_dir"] = str(args.image_dir)

    settings = load_settings(args.config_file, overrides)
    db_path = Path(settings.db_path)
    raw_dir = Path(settings.raw_dir)
    image_dir = Path(settings.image_dir)

    if not db_path.exists():
        print(f"Error: database file not found at '{db_path}'.", file=sys.stderr)
        return 1

    if args.output:
        output_dir = Path(args.output)
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = Path("backups") / f"backup_{timestamp}"

    try:
        manifest = create_backup(
            db_path=db_path,
            output_dir=output_dir,
            raw_dir=raw_dir,
            image_dir=image_dir,
        )
    except StorageError as exc:
        print(f"Backup failed [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(manifest.to_dict(), indent=2))
    else:
        print(f"Backup created successfully at: {output_dir}")
        print(f"  Schema version: {manifest.schema_version}")
        print(f"  Index version:  {manifest.index_version}")
        print(f"  Total files:    {manifest.total_files}")
        print(f"  Total bytes:    {manifest.total_bytes}")
        print(f"  Manifest:       {output_dir / 'manifest.json'}")
    return 0


def _restore(args: argparse.Namespace) -> int:
    from search_engine.operations.backup import restore_backup

    backup_dir = Path(args.backup_dir)
    target_dir = Path(args.target_dir)

    if not backup_dir.exists() or not backup_dir.is_dir():
        print(f"Error: backup directory '{backup_dir}' does not exist.", file=sys.stderr)
        return 1

    try:
        result = restore_backup(backup_dir=backup_dir, target_dir=target_dir)
    except StorageError as exc:
        print(f"Restore failed [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Restore completed successfully into: {result.target_dir}")
        print(f"  Database path:  {result.database_path}")
        print(f"  Files restored: {result.files_restored}")
        print(f"  Bytes restored: {result.bytes_restored}")
        print(f"  Integrity:      {'PASSED' if result.integrity_ok else 'FAILED'}")
        print(f"  Consistency:    {'PASSED' if result.consistency.is_consistent else 'FAILED'}")
    return 0


def _block(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import block_target

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()
    try:
        rec = block_target(db, args.target, args.kind, reason=args.reason)
        print(f"Blocked {rec.kind}: {rec.target} (ID: {rec.id})")
        return 0
    finally:
        db.close()


def _unblock(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import unblock_target

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()
    try:
        ok = unblock_target(db, args.target, args.kind)
        if ok:
            print(f"Unblocked {args.kind}: {args.target}")
            return 0
        print(f"Target '{args.target}' was not found in blocks.", file=sys.stderr)
        return 1
    finally:
        db.close()


def _blocks(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import list_blocks

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()
    try:
        recs = list_blocks(db)
        if args.json:
            print(json.dumps([r.to_dict() for r in recs], indent=2))
        else:
            if not recs:
                print("No active operator blocks.")
            else:
                for r in recs:
                    print(
                        f"[{r.kind.upper()}] {r.target} "
                        f"(reason: {r.reason}, created: {r.created_at})"
                    )
        return 0
    finally:
        db.close()


def _remove(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import remove_document
    from search_engine.storage import FileStore

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()
    store = FileStore(Path(settings.raw_dir).parent)
    try:
        rep = remove_document(
            db,
            store=store if args.purge_raw else None,
            url=args.url,
            domain=args.domain,
            purge_raw=args.purge_raw,
            reason=args.reason,
        )
        if args.json:
            print(json.dumps(rep.to_dict(), indent=2))
        else:
            print(f"Removal complete for {rep.kind}: {rep.target}")
            print(f"  URLs blocked:       {rep.urls_blocked}")
            print(f"  Documents removed:  {rep.documents_removed}")
            print(f"  FTS index cleaned:  {rep.fts_rows_deleted} rows")
            if args.purge_raw:
                print(f"  Raw files purged:   {rep.raw_files_purged}")
        return 0
    finally:
        db.close()


def _images(args: argparse.Namespace) -> int:
    if args.images_command == "takedown":
        from search_engine.operations.controls import takedown_image
        from search_engine.storage import FileStore

        overrides = _parse_overrides(args.overrides)
        if args.db:
            overrides["db_path"] = str(args.db)
        settings = load_settings(args.config_file, overrides)
        db = Database(Path(settings.db_path)).open()
        store = FileStore(Path(settings.image_dir).parent)
        try:
            ok = takedown_image(db, store, args.id, reason=args.reason)
            if ok:
                print(f"Image '{args.id}' takedown completed (files deleted).")
                return 0
            print(f"Image '{args.id}' not found.", file=sys.stderr)
            return 1
        finally:
            db.close()
    return 2


def _entities(args: argparse.Namespace) -> int:
    if args.entities_command == "remove":
        from search_engine.operations.controls import remove_entity

        overrides = _parse_overrides(args.overrides)
        if args.db:
            overrides["db_path"] = str(args.db)
        settings = load_settings(args.config_file, overrides)
        db = Database(Path(settings.db_path)).open()
        try:
            ok = remove_entity(db, args.id)
            if ok:
                print(f"Entity '{args.id}' removed from store.")
                return 0
            print(f"Entity '{args.id}' not found.", file=sys.stderr)
            return 1
        finally:
            db.close()
    return 2


def _pause(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import pause_crawler

    overrides = _parse_overrides(args.overrides)
    settings = load_settings(args.config_file, overrides)
    data_dir = args.data_dir or Path(settings.db_path).parent.parent
    p = pause_crawler(data_dir)
    print(f"Crawl pause requested (flag created at {p}).")
    return 0


def _resume(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import resume_crawler

    overrides = _parse_overrides(args.overrides)
    settings = load_settings(args.config_file, overrides)
    data_dir = args.data_dir or Path(settings.db_path).parent.parent
    ok = resume_crawler(data_dir)
    if ok:
        print("Crawl resume requested (PAUSE file removed).")
    else:
        print("Crawler was not paused (no PAUSE file).")
    return 0


def _stop(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import stop_crawler

    overrides = _parse_overrides(args.overrides)
    settings = load_settings(args.config_file, overrides)
    data_dir = args.data_dir or Path(settings.db_path).parent.parent
    p = stop_crawler(data_dir)
    print(f"Crawl stop requested (flag created at {p}).")
    return 0


def _status(args: argparse.Namespace) -> int:
    from search_engine.operations.controls import check_engine_status

    overrides = _parse_overrides(args.overrides)
    if args.db:
        overrides["db_path"] = str(args.db)
    settings = load_settings(args.config_file, overrides)
    db = Database(Path(settings.db_path)).open()
    try:
        rep = check_engine_status(settings, db)
        if args.json:
            print(json.dumps(rep.to_dict(), indent=2))
        else:
            print(f"Engine Status:      {rep.status.upper()}")
            print(f"  Paused:           {rep.paused}")
            print(f"  Stopped:          {rep.stopped}")
            print(f"  Free Disk:        {rep.free_disk_bytes / (1024 * 1024):.1f} MB")
            print(f"  Active Documents: {rep.active_documents}")
            print(f"  Active News:      {rep.active_news_items}")
            print(f"  Active Images:    {rep.active_images}")
            print(f"  Operator Blocks:  {rep.active_blocks}")
            if rep.warnings:
                print("Warnings:")
                for w in rep.warnings:
                    print(f"  - {w}")
        return 0
    finally:
        db.close()


def _retain(args: argparse.Namespace) -> int:
    if args.retain_command == "purge":
        from search_engine.operations.retention import apply_retention_purge
        from search_engine.storage import FileStore

        overrides = _parse_overrides(args.overrides)
        if args.db:
            overrides["db_path"] = str(args.db)
        settings = load_settings(args.config_file, overrides)
        db = Database(Path(settings.db_path)).open()
        store = FileStore(Path(settings.raw_dir).parent)
        try:
            rep = apply_retention_purge(
                db,
                store=store,
                news_retention_days=args.days,
                news_max_items=args.cap,
            )
            if args.json:
                print(json.dumps(rep.to_dict(), indent=2))
            else:
                print("Retention Purge Complete:")
                print(f"  Expired news items:       {rep.news_items_expired}")
                print(f"  Cap-pruned news items:    {rep.news_items_pruned_for_cap}")
                print(f"  Deindexed documents:      {rep.documents_deindexed}")
                print(f"  Stale temp files removed: {rep.temp_files_removed}")
                if rep.details:
                    print("Details:")
                    for d in rep.details:
                        print(f"  - {d}")
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
        if args.command == "backup":
            return _backup(args)
        if args.command == "restore":
            return _restore(args)
        if args.command == "block":
            return _block(args)
        if args.command == "unblock":
            return _unblock(args)
        if args.command == "blocks":
            return _blocks(args)
        if args.command == "remove":
            return _remove(args)
        if args.command == "images":
            return _images(args)
        if args.command == "entities":
            return _entities(args)
        if args.command == "pause":
            return _pause(args)
        if args.command == "resume":
            return _resume(args)
        if args.command == "stop":
            return _stop(args)
        if args.command == "status":
            return _status(args)
        if args.command == "retain":
            return _retain(args)
    except ConfigError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 3
    except PolicyDenied as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 4
    except StorageError as exc:
        print(f"error [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1
    return 2  # pragma: no cover - argparse rejects unknown commands first


if __name__ == "__main__":
    sys.exit(main())
