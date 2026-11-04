"""Event reason codes and the event record. Later steps extend this module; never fork it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Reason(StrEnum):
    ACCEPTED = "accepted"
    BLOCKED_POLICY = "blocked_policy"
    BLOCKED_ROBOTS = "blocked_robots"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_LIMIT = "skipped_limit"
    FETCHED = "fetched"
    FAILED = "failed"
    STORED = "stored"
    UNSUPPORTED = "unsupported"
    NOINDEX = "noindex"
    IMAGE_REJECTED = "image_rejected"
    NEWS_STALE = "news_stale"


@dataclass(frozen=True)
class Event:
    reason: Reason
    subject: str
    detail: str
    at: datetime
