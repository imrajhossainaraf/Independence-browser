"""RunReport: aggregates events so every URL or item is accounted for with a reason."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from search_engine.logs import redact_text
from search_engine.reporting.events import Event, Reason


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RunReport:
    def __init__(
        self,
        run_id: str,
        *,
        clock: Callable[[], datetime] = _utc_now,
        max_events: int = 10_000,
    ) -> None:
        self.run_id = run_id
        self._clock = clock
        self._max_events = max_events
        self.started_at = clock()
        self.finished_at: datetime | None = None
        self._counts: Counter[Reason] = Counter()
        self.events: list[Event] = []
        self.dropped_events = 0

    def record(self, reason: Reason, subject: str, detail: str = "") -> Event:
        """Count an outcome. Subjects and details are scrubbed of credentials before storing."""
        event = Event(reason, redact_text(subject), redact_text(detail), self._clock())
        self._counts[reason] += 1
        if len(self.events) < self._max_events:
            self.events.append(event)
        else:
            self.dropped_events += 1
        return event

    def finish(self) -> None:
        self.finished_at = self._clock()

    def count(self, reason: Reason) -> int:
        return self._counts[reason]

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def counts(self) -> dict[str, int]:
        return {reason.value: self._counts[reason] for reason in Reason if self._counts[reason]}

    def reconciles(self, expected_total: int) -> bool:
        """True when the recorded outcomes account for exactly ``expected_total`` items."""
        return self.total == expected_total

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or self._clock()
        return max(0.0, (end - self.started_at).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "total": self.total,
            "counts": self.counts(),
            "dropped_events": self.dropped_events,
        }

    def render(self) -> str:
        lines = [
            f"Run report {self.run_id}",
            f"Duration: {self.duration_seconds:.1f} s",
        ]
        if self.total == 0:
            lines.append("No events recorded.")
            return "\n".join(lines)
        width = max(len(name) for name in self.counts())
        for name, number in self.counts().items():
            lines.append(f"  {name.ljust(width)}  {number}")
        lines.append(f"Total: {self.total}")
        if self.dropped_events:
            lines.append(f"({self.dropped_events} events counted but not stored)")
        return "\n".join(lines)
