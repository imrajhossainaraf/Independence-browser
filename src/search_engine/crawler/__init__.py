"""Crawler: normalization, scheduling, and SafeFetcher."""

from __future__ import annotations

from search_engine.crawler.approvals import (
    FetchApproval,
    RobotsFetchApproval,
    issue_fetch_approval,
    issue_robots_approval,
)
from search_engine.crawler.fetcher import (
    FetchResult,
    Redirect,
    SafeFetcher,
    sniff_content,
)

__all__ = [
    "FetchApproval",
    "FetchResult",
    "Redirect",
    "RobotsFetchApproval",
    "SafeFetcher",
    "issue_fetch_approval",
    "issue_robots_approval",
    "sniff_content",
]
