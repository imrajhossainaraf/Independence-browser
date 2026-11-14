"""Crawler: normalization, scheduling, robots, and SafeFetcher."""

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
from search_engine.crawler.robots import (
    AllowAllRobotsPolicy,
    DisallowAllRobotsPolicy,
    ProtegoRobotsPolicy,
    RobotsCache,
    RobotsPolicy,
)
from search_engine.crawler.scheduler import (
    Denial,
    Scheduler,
)

__all__ = [
    "AllowAllRobotsPolicy",
    "Denial",
    "DisallowAllRobotsPolicy",
    "FetchApproval",
    "FetchResult",
    "ProtegoRobotsPolicy",
    "Redirect",
    "RobotsCache",
    "RobotsFetchApproval",
    "RobotsPolicy",
    "SafeFetcher",
    "Scheduler",
    "issue_fetch_approval",
    "issue_robots_approval",
    "sniff_content",
]
