"""Controlled crawl pipeline with safety controls and reconciling report (Step 16).

Connects Frontier, Scheduler, SafeFetcher, Adapters, Storage, and Indexer into
a bounded, resumable, stoppable crawl with Windows-safe signals, STOP file,
disk space guard, and complete URL accounting.
"""

from __future__ import annotations

import logging
import shutil
import signal
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from search_engine.config import Settings
from search_engine.crawler.blocklist import Blocklist
from search_engine.crawler.fetcher import FetchResult, Redirect, SafeFetcher
from search_engine.crawler.frontier import Frontier, FrontierConfig, QueuedUrl
from search_engine.crawler.normalize import normalize
from search_engine.crawler.preflight import verify_preflight_approval
from search_engine.crawler.robots import RobotsCache
from search_engine.crawler.scheduler import Denial, Scheduler
from search_engine.crawler.sources import build_allowlist, parse_seeds, read_lines
from search_engine.db import Database
from search_engine.extract.adapters import extract_content
from search_engine.extract.models import AdapterStatus
from search_engine.indexer.build import upsert_document_index
from search_engine.net.httpx_transport import HttpxTransport
from search_engine.net.resolver import Resolver, SystemResolver
from search_engine.net.transport import Transport
from search_engine.reporting.events import Reason
from search_engine.reporting.reports import RunReport
from search_engine.repositories import DocumentRepository
from search_engine.storage import FileStore

logger = logging.getLogger(__name__)

# Minimum free disk space before crawl automatically pauses/halts (50 MiB)
DEFAULT_MIN_FREE_DISK_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class PipelineResult:
    """Summary outcome of a crawl pipeline run."""

    run_id: str
    status: str  # e.g. "completed", "stop_file", "interrupted", "disk_low"
    report: RunReport
    frontier_stats: dict[str, int]
    pages_stored: int
    duration_seconds: float


class CrawlPipeline:
    """Bounded, stoppable, resumable web crawler pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        mode: str = "fixture",
        db: Database | None = None,
        file_store: FileStore | None = None,
        resolver: Resolver | None = None,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        min_free_disk_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES,
    ) -> None:
        if mode not in ("fixture", "real"):
            raise ValueError(f"Invalid crawl mode: {mode!r}; expected 'fixture' or 'real'")

        self.settings = settings
        self.mode = mode
        self._clock = clock
        self.min_free_disk_bytes = min_free_disk_bytes

        # Verify real mode safety gate
        if self.mode == "real":
            verify_preflight_approval(self.settings)

        self.data_dir = self.settings.seeds_file.parent
        self.db = db or Database(self.settings.db_path).open()
        self.file_store = file_store or FileStore(self.data_dir)
        self.resolver = resolver or SystemResolver()

        # Transport: allow loopback only in fixture mode
        allow_loopback = (self.mode == "fixture") or self.settings.allow_loopback_for_tests
        self.transport = transport or HttpxTransport(allow_loopback=allow_loopback)
        self.fetcher = SafeFetcher(self.transport, self.settings)

        self.robots_cache = RobotsCache()

        # Build allowlist and blocklist
        self.blocklist = Blocklist()
        self.allowlist_domains: set[str] = set()

        if self.settings.allowlist_mode:
            seeds_path = self.settings.seeds_file
            if seeds_path.exists():
                seed_res = parse_seeds(read_lines(seeds_path), allow_loopback=allow_loopback)
                self.allowlist_domains = set(build_allowlist(seed_res.entries))

        self.frontier_config = FrontierConfig.from_settings(
            self.settings,
            allowlist_domains=self.allowlist_domains,
            blocklist=self.blocklist,
        )
        self.frontier_config.allow_loopback = allow_loopback

        self.scheduler = Scheduler(
            self.settings,
            self.resolver,
            self.robots_cache,
            allowlist=self.allowlist_domains,
            blocklist=self.blocklist,
            monotonic_clock=self._clock,
            fetcher_ref=self.fetcher,
        )

        self._stop_requested = False
        self._stop_file = self.data_dir / "STOP"

    def _check_disk_space(self) -> bool:
        """Return True if sufficient disk space is available."""
        try:
            stat = shutil.disk_usage(self.data_dir)
            if stat.free < self.min_free_disk_bytes:
                logger.warning(
                    "Low disk space: %d bytes free (minimum %d bytes required)",
                    stat.free,
                    self.min_free_disk_bytes,
                )
                return False
            return True
        except OSError:
            return True

    def _register_signals(self) -> tuple[Any, Any, Any]:
        """Register Windows-safe signal handlers (SIGINT, SIGBREAK, SIGTERM)."""

        def handler(signum: int, frame: Any) -> None:
            logger.info("Signal %d received, requesting graceful shutdown...", signum)
            self._stop_requested = True

        prev_int = signal.signal(signal.SIGINT, handler)
        prev_break = None
        if hasattr(signal, "SIGBREAK"):
            prev_break = signal.signal(signal.SIGBREAK, handler)
        prev_term = None
        if hasattr(signal, "SIGTERM"):
            prev_term = signal.signal(signal.SIGTERM, handler)

        return prev_int, prev_break, prev_term

    def _restore_signals(self, saved: tuple[Any, Any, Any]) -> None:
        """Restore previous signal handlers."""
        prev_int, prev_break, prev_term = saved
        if prev_int is not None:
            signal.signal(signal.SIGINT, prev_int)
        if prev_break is not None and hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, prev_break)
        if prev_term is not None and hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, prev_term)

    def run(
        self,
        seeds: Sequence[str] | None = None,
        *,
        run_id: str | None = None,
        max_pages: int | None = None,
        max_time_seconds: float | None = None,
    ) -> PipelineResult:
        """Execute the crawl pipeline until limits, empty queue, STOP file, or signal."""
        t0 = self._clock()
        run_id = run_id or f"crawl-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        report = RunReport(run_id)

        target_max_pages = max_pages or self.settings.max_pages
        target_max_time = max_time_seconds or float(self.settings.max_runtime_seconds)

        # Clean any leftover STOP file before new crawl start if caller requests, or check
        if self._stop_file.exists():
            # If STOP file already exists before starting, halt immediately
            report.finish()
            return PipelineResult(
                run_id=run_id,
                status="stop_file",
                report=report,
                frontier_stats={},
                pages_stored=0,
                duration_seconds=0.0,
            )

        saved_signals = self._register_signals()

        with self.db.transaction() as conn:
            frontier = Frontier(conn, self.frontier_config)
            doc_repo = DocumentRepository(conn)

            # Cold start resume: reset any in-flight leases back to pending
            frontier.reset_leases()

            # Seed ingestion
            if seeds:
                for seed_url in seeds:
                    try:
                        norm = normalize(
                            seed_url,
                            allow_loopback=self.frontier_config.allow_loopback,
                        )
                        self.allowlist_domains.add(norm.registered_domain)
                        res = frontier.add_seed(norm)
                        if res.created:
                            report.record(Reason.ACCEPTED, norm.key, "seed_added")
                        else:
                            report.record(Reason.SKIPPED_DUPLICATE, norm.key, "seed_already_exists")
                    except Exception as exc:  # noqa: BLE001
                        report.record(Reason.BLOCKED_POLICY, seed_url, f"seed_rejected: {exc}")

            pages_stored_initial = int(
                conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'active'").fetchone()[0]
            )

            status = "completed"

            try:
                while not self._stop_requested:
                    # 1. Safety check: STOP file
                    if self._stop_file.exists():
                        logger.info("STOP file detected at %s. Halting crawl.", self._stop_file)
                        status = "stop_file"
                        break

                    # 2. Safety check: Disk space
                    if not self._check_disk_space():
                        logger.warning("Low disk space guard triggered. Halting crawl.")
                        report.record(Reason.FAILED, "storage", "low_disk_space_guard")
                        status = "disk_low"
                        break

                    # 3. Limits: Runtime
                    elapsed = self._clock() - t0
                    if elapsed >= target_max_time:
                        logger.info("Maximum crawl time reached (%.1fs).", elapsed)
                        status = "time_limit"
                        break

                    # 4. Limits: Page count
                    current_pages = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM documents WHERE status = 'active'"
                        ).fetchone()[0]
                    )
                    if current_pages >= target_max_pages:
                        logger.info("Maximum page cap reached (%d).", current_pages)
                        status = "page_limit"
                        break

                    # 5. Lease next URL from frontier
                    leased = frontier.lease_next(batch_size=1)
                    if not leased:
                        if frontier.has_pending():
                            retry_delay = frontier.next_retry_delay()
                            sleep_s = min(
                                0.5, max(0.05, retry_delay if retry_delay is not None else 0.1)
                            )
                            time.sleep(sleep_s)
                            continue
                        # Nothing pending or retrying
                        status = "completed"
                        break

                    item = leased[0]
                    self._process_item(
                        item=item,
                        conn=conn,
                        frontier=frontier,
                        doc_repo=doc_repo,
                        report=report,
                    )

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received. Halting crawl.")
                status = "interrupted"
            finally:
                self._restore_signals(saved_signals)
                report.finish()

            frontier_stats = frontier.stats()
            pages_stored_final = int(
                conn.execute("SELECT COUNT(*) FROM documents WHERE status = 'active'").fetchone()[0]
            )

        duration = self._clock() - t0
        return PipelineResult(
            run_id=run_id,
            status=status,
            report=report,
            frontier_stats=frontier_stats,
            pages_stored=pages_stored_final - pages_stored_initial,
            duration_seconds=duration,
        )

    def _process_item(
        self,
        *,
        item: QueuedUrl,
        conn: Any,
        frontier: Frontier,
        doc_repo: DocumentRepository,
        report: RunReport,
    ) -> None:
        """Fetch, validate, store, and discover links for a leased URL."""
        url = item.url_key
        iso_now = datetime.now(UTC).isoformat()

        # Step 1: Scheduler approval (DNS, IP policy, robots, delay, concurrency)
        approval = self.scheduler.approve(url, kind="page")
        if isinstance(approval, Denial):
            if approval.reason == "politeness_delay":
                wait_s = approval.retry_after_seconds or 0.1
                if wait_s <= 2.0:
                    time.sleep(wait_s)
                    approval = self.scheduler.approve(url, kind="page")

        if isinstance(approval, Denial):
            if approval.reason == "robots_disallowed":
                frontier.skip(item.id, reason="robots_disallowed")
                report.record(Reason.BLOCKED_ROBOTS, url, "robots_disallowed")
            elif approval.reason in ("politeness_delay", "host_concurrency_limit"):
                # Non-fatal temporary denial; return to retry
                frontier.retry(item.id, reason=approval.reason)
            else:
                frontier.block(item.id, reason=approval.reason)
                report.record(Reason.BLOCKED_POLICY, url, approval.reason)
            return

        # Step 2: Safe fetch
        res = self.fetcher.fetch_once(approval)
        status_code = res.status_code if isinstance(res, (FetchResult, Redirect)) else 0
        is_retry = isinstance(res, FetchResult) and res.outcome == "retryable"
        self.scheduler.release(
            approval,
            status_code=status_code,
            retry=is_retry,
        )

        # Step 3: Handle Redirects
        if isinstance(res, Redirect):
            target_url = res.target
            # Redirect target goes through full submission and approval path
            add_res = frontier.add_discovered_url(
                target_url,
                source="redirect",
                parent_url_id=item.id,
                depth=item.depth,
            )
            if add_res.status == "pending":
                report.record(Reason.ACCEPTED, target_url, f"redirect_from_{url}")
            elif add_res.status == "blocked":
                report.record(Reason.BLOCKED_POLICY, target_url, add_res.reason or "blocked")
            elif add_res.status == "skipped":
                report.record(Reason.SKIPPED_LIMIT, target_url, add_res.reason or "skipped")

            # Complete the original hopped URL
            frontier.complete(item.id)
            return

        # Step 4: Handle Fetch Results
        if not isinstance(res, FetchResult):
            frontier.fail_permanent(item.id, reason="unknown_fetch_result")
            report.record(Reason.FAILED, url, "unknown_fetch_result")
            return

        if res.outcome == "retryable":
            frontier.retry(item.id, reason=f"http_{res.status_code}")
            report.record(Reason.FAILED, url, f"status_{res.status_code}_retryable")
            return

        if res.outcome in ("permanent", "rejected"):
            frontier.fail_permanent(item.id, reason=f"http_{res.status_code}")
            report.record(Reason.FAILED, url, f"status_{res.status_code}_{res.outcome}")
            return

        # Fetch succeeded!
        report.record(Reason.FETCHED, url, f"status_{res.status_code}")

        # Step 5: Store raw payload
        raw_hash = self.file_store.put_raw(res.body)

        # Step 6: Extract content via adapters
        extract_res = extract_content(
            res.body,
            final_url=res.url,
            content_type=res.content_type,
        )
        doc = extract_res.document

        if extract_res.status == AdapterStatus.UNSUPPORTED:
            frontier.complete(item.id)
            report.record(Reason.UNSUPPORTED, url, "unsupported_content_type")
            return

        # Check robots meta noindex directive
        is_noindex = extract_res.status == AdapterStatus.NOINDEX or (
            doc is not None and doc.robots_meta.noindex
        )
        doc_status = "noindex" if is_noindex else "active"

        # Compress text for storage
        text_gz = (
            self.file_store.compress_text(doc.text)
            if (doc and hasattr(self.file_store, "compress_text"))
            else None
        )
        if text_gz is None and doc and doc.text:
            import gzip

            text_gz = gzip.compress(doc.text.encode("utf-8"), mtime=0)

        # Store in documents table
        doc_id = doc_repo.upsert_document(
            url_id=item.id,
            doc_type=extract_res.doc_type if extract_res.doc_type != "unknown" else "page",
            final_url=res.url,
            canonical_url=doc.canonical_url if doc else None,
            title=doc.title if doc else "",
            headings=doc.headings_text if doc else "",
            description=doc.description if doc else "",
            text_gz=text_gz,
            lang=doc.lang if doc else "und",
            status=doc_status,
            word_count=doc.word_count if doc else 0,
            content_hash=doc.content_hash if doc else None,
            raw_hash=raw_hash,
            published_at=doc.published_at if doc else None,
            fetched_at=iso_now,
        )

        # Update FTS Index (skips noindex automatically)
        upsert_document_index(conn, doc_id)

        if is_noindex:
            report.record(Reason.NOINDEX, url, "robots_meta_noindex")
        else:
            report.record(Reason.STORED, url, "stored_and_indexed")

        # Step 7: Discover links (if not nofollow)
        nofollow_page = doc.robots_meta.nofollow if doc else False
        if not nofollow_page and doc and doc.outgoing_links:
            for link in doc.outgoing_links:
                link_res = frontier.add_discovered_url(
                    link.url,
                    source="link",
                    parent_url_id=item.id,
                    depth=item.depth + 1,
                    nofollow=link.nofollow,
                )
                if link_res.created and link_res.status == "pending":
                    report.record(Reason.ACCEPTED, link.url, "link_discovered")
                elif link_res.status == "blocked":
                    report.record(Reason.BLOCKED_POLICY, link.url, link_res.reason or "blocked")
                elif link_res.status == "skipped":
                    limit_reasons = (
                        "host_cap_reached",
                        "global_cap_reached",
                        "depth_exceeded",
                    )
                    if link_res.reason in limit_reasons:
                        report.record(Reason.SKIPPED_LIMIT, link.url, link_res.reason or "limit")
                    else:
                        report.record(Reason.BLOCKED_POLICY, link.url, link_res.reason or "skipped")
                elif not link_res.created:
                    report.record(Reason.SKIPPED_DUPLICATE, link.url, "already_seen")

        # Discover sitemap URLs if sitemap was parsed
        for s_url in extract_res.sitemap_urls:
            s_res = frontier.add_discovered_url(
                s_url,
                source="sitemap",
                parent_url_id=item.id,
                depth=item.depth + 1,
            )
            if s_res.created and s_res.status == "pending":
                report.record(Reason.ACCEPTED, s_url, "sitemap_url")

        # Discover feed items if feed was parsed
        for item_cand in extract_res.news_items:
            f_res = frontier.add_discovered_url(
                item_cand.link,
                source="feed",
                parent_url_id=item.id,
                depth=item.depth + 1,
            )
            if f_res.created and f_res.status == "pending":
                report.record(Reason.ACCEPTED, item_cand.link, "feed_url")

        # Mark URL complete in frontier
        frontier.complete(item.id)
