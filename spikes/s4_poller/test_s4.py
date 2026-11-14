"""Tests for Spike S4: Concurrency in WAL mode and Windows stale lock recovery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from search_engine.db import Database
from spikes.s4_poller.benchmark import run_poller_server_benchmark
from spikes.s4_poller.lock import LockHeldError, ProcessLock


def test_wal_concurrency_read_latency(tmp_path: Path) -> None:
    """Simultaneous writer loop (news poller) and reader loop (server) in WAL mode.

    Verifies:
    - 0 read errors, 0 write errors
    - p95 read latency under heavy concurrent writes remains fast (< 50ms)
    """
    db_path = tmp_path / "wal_concurrency.db"
    db = Database(db_path).open()
    try:
        stats = run_poller_server_benchmark(db, duration_seconds=1.5, write_batch_size=5)
        assert stats.read_errors == 0
        assert stats.write_errors == 0
        assert stats.total_reads > 20
        assert stats.total_writes > 20
        # In SQLite WAL mode, readers do not block writers and writers do not block readers.
        assert stats.p95_ms < 50.0
    finally:
        db.close()


def test_process_lock_mutual_exclusion(tmp_path: Path) -> None:
    """Active process lock prevents concurrent acquisition."""
    lock_file = tmp_path / "test.lock"
    lock1 = ProcessLock(lock_file, timeout_seconds=0.0)
    lock2 = ProcessLock(lock_file, timeout_seconds=0.05)

    assert lock1.acquire()
    assert lock1.is_locked

    # Second lock fails
    with pytest.raises(LockHeldError):
        lock2.acquire()

    lock1.release()
    assert not lock1.is_locked

    # Second lock now succeeds
    assert lock2.acquire()
    assert lock2.is_locked
    lock2.release()


def test_stale_lock_recovery_dead_pid(tmp_path: Path) -> None:
    """Stale lock file with nonexistent PID is automatically recovered."""
    lock_file = tmp_path / "stale.lock"
    # Write a fake metadata file for an impossible PID
    dead_pid = 999999
    lock_file.write_text(
        f'{{"pid": {dead_pid}, "acquired_at": "2025-01-01T00:00:00Z"}}\n',
        encoding="utf-8",
    )

    lock = ProcessLock(lock_file, timeout_seconds=0.1)
    assert not lock._is_holder_alive()

    # Recovery: acquire breaks the dead lock cleanly
    assert lock.acquire()
    assert lock.is_locked
    lock.release()


def test_killed_subprocess_lock_recovery(tmp_path: Path) -> None:
    """When a process holding the lock is killed, subsequent process recovers immediately."""
    lock_file = tmp_path / "child.lock"

    # Spawn helper child that acquires lock and sleeps
    code = (
        "import time, sys\n"
        "from pathlib import Path\n"
        "from spikes.s4_poller.lock import ProcessLock\n"
        f"lock = ProcessLock(Path(r'{lock_file}'), timeout_seconds=1.0)\n"
        "lock.acquire()\n"
        "sys.stdout.write('LOCKED\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # Wait for child to lock
        line = proc.stdout.readline() if proc.stdout else ""
        assert "LOCKED" in line

        # Parent cannot acquire while child is alive
        parent_lock = ProcessLock(lock_file, timeout_seconds=0.05)
        with pytest.raises(LockHeldError):
            parent_lock.acquire()

        # Kill child ungracefully (SIGKILL / TerminateProcess)
        proc.kill()
        proc.wait(timeout=5.0)

        # On Windows, killing the process immediately releases the OS lock handle.
        # Parent acquires successfully with zero errors!
        assert parent_lock.acquire()
        assert parent_lock.is_locked
        parent_lock.release()
    finally:
        if proc.poll() is None:
            proc.kill()
