from __future__ import annotations

from datetime import UTC, datetime, timedelta

from search_engine.reporting import Reason, RunReport


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_reason_codes_match_the_plan() -> None:
    assert {r.value for r in Reason} == {
        "accepted",
        "blocked_policy",
        "blocked_robots",
        "skipped_duplicate",
        "skipped_limit",
        "fetched",
        "failed",
        "stored",
        "unsupported",
        "noindex",
        "image_rejected",
        "news_stale",
    }


def test_zero_data_report_is_readable() -> None:
    report = RunReport("r0", clock=FakeClock())
    assert report.total == 0
    assert report.counts() == {}
    assert report.reconciles(0)
    assert "No events recorded." in report.render()
    assert report.to_dict()["total"] == 0


def test_nonzero_report_aggregates_and_renders() -> None:
    clock = FakeClock()
    report = RunReport("r1", clock=clock)
    report.record(Reason.FETCHED, "https://example.org/a")
    report.record(Reason.FETCHED, "https://example.org/b")
    report.record(Reason.BLOCKED_ROBOTS, "https://example.org/private", "disallowed")
    clock.advance(12.5)
    report.finish()
    assert report.count(Reason.FETCHED) == 2
    assert report.counts() == {"blocked_robots": 1, "fetched": 2}
    assert report.total == 3
    assert report.reconciles(3) and not report.reconciles(4)
    assert report.duration_seconds == 12.5
    text = report.render()
    assert "fetched" in text and "blocked_robots" in text and "Total: 3" in text
    data = report.to_dict()
    assert data["finished_at"] is not None
    assert data["counts"] == {"blocked_robots": 1, "fetched": 2}


def test_event_text_is_scrubbed_of_credentials() -> None:
    report = RunReport("r2", clock=FakeClock())
    event = report.record(Reason.FAILED, "https://example.org", "Authorization: Bearer secret99")
    assert "secret99" not in event.detail


def test_event_storage_is_capped_but_counts_stay_exact() -> None:
    report = RunReport("r3", clock=FakeClock(), max_events=2)
    for number in range(5):
        report.record(Reason.ACCEPTED, f"u{number}")
    assert report.total == 5
    assert len(report.events) == 2
    assert report.dropped_events == 3
    assert "3 events counted but not stored" in report.render()
