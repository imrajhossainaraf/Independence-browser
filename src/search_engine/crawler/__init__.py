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
from search_engine.crawler.frontier import (
    AddResult,
    Frontier,
    FrontierConfig,
    FrontierEntry,
    QueuedUrl,
    calculate_priority,
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
    "AddResult",
    "AllowAllRobotsPolicy",
    "Denial",
    "DisallowAllRobotsPolicy",
    "FetchApproval",
    "FetchResult",
    "Frontier",
    "FrontierConfig",
    "FrontierEntry",
    "ProtegoRobotsPolicy",
    "QueuedUrl",
    "Redirect",
    "RobotsCache",
    "RobotsFetchApproval",
    "RobotsPolicy",
    "SafeFetcher",
    "Scheduler",
    "calculate_priority",
    "issue_fetch_approval",
    "issue_robots_approval",
    "sniff_content",
]
