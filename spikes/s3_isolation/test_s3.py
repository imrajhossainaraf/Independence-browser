"""Unit tests for Spike S3 isolation prototypes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from spikes.s3_isolation.job_object import WindowsJobObject
from spikes.s3_isolation.psutil_monitor import run_monitored_process

WORKER_PY = str(Path(__file__).parent / "worker.py")


def test_windows_job_object_enforces_memory_limit() -> None:
    """Windows Job Object limits committed memory and raises MemoryError in child."""
    with WindowsJobObject(memory_limit_bytes=30 * 1024 * 1024) as job:
        proc = subprocess.Popen(
            [sys.executable, WORKER_PY, "leak"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert job.assign_process(int(proc._handle))
        stdout, _ = proc.communicate(timeout=5)
        # Worker caught MemoryError (exit 42) or crashed under commit limit
        assert "MEMORY_ERROR" in stdout or proc.returncode != 0


def test_psutil_monitor_kills_memory_leak() -> None:
    """psutil monitoring detects RSS limit breach and kills the process."""
    res = run_monitored_process(
        [sys.executable, WORKER_PY, "leak"],
        timeout_seconds=5.0,
        memory_limit_bytes=40 * 1024 * 1024,
        poll_interval=0.01,
    )
    assert res.killed_by_memory is True


def test_psutil_monitor_kills_hung_process_on_timeout() -> None:
    """psutil monitoring terminates a hung infinite loop process upon timeout."""
    res = run_monitored_process(
        [sys.executable, WORKER_PY, "hang"],
        timeout_seconds=0.4,
        poll_interval=0.02,
    )
    assert res.killed_by_timeout is True


def test_psutil_monitor_kills_child_process_tree() -> None:
    """psutil monitoring terminates the entire process tree on timeout."""
    res = run_monitored_process(
        [sys.executable, WORKER_PY, "tree"],
        timeout_seconds=0.4,
        poll_interval=0.02,
    )
    assert res.killed_by_timeout is True


def test_normal_execution_succeeds_without_interference() -> None:
    """A normal, polite worker task completes successfully under monitoring."""
    res = run_monitored_process(
        [sys.executable, WORKER_PY, "echo"],
        input_text="hello world\n",
        timeout_seconds=2.0,
        memory_limit_bytes=100 * 1024 * 1024,
    )
    assert res.returncode == 0
    assert not res.killed_by_timeout
    assert not res.killed_by_memory
    assert "ECHO: hello world" in res.stdout
