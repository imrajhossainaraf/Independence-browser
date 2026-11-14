"""Benchmark comparing Windows Job Object and psutil monitoring for Spike S3."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from spikes.s3_isolation.job_object import WindowsJobObject
from spikes.s3_isolation.psutil_monitor import run_monitored_process

WORKER_PY = str(Path(__file__).parent / "worker.py")


def measure_startup_overhead() -> dict[str, float]:
    iterations = 5
    results: dict[str, float] = {}

    # 1. Bare Popen
    t0 = time.monotonic()
    for _ in range(iterations):
        proc = subprocess.Popen(
            [sys.executable, WORKER_PY, "echo"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        proc.communicate("hello\n")
    results["bare_popen_ms"] = ((time.monotonic() - t0) / iterations) * 1000

    # 2. Windows Job Object
    t0 = time.monotonic()
    for _ in range(iterations):
        with WindowsJobObject(memory_limit_bytes=100 * 1024 * 1024) as job:
            proc = subprocess.Popen(
                [sys.executable, WORKER_PY, "echo"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
            )
            job.assign_process(int(proc._handle))
            proc.communicate("hello\n")
    results["job_object_ms"] = ((time.monotonic() - t0) / iterations) * 1000

    # 3. psutil monitor
    t0 = time.monotonic()
    for _ in range(iterations):
        run_monitored_process(
            [sys.executable, WORKER_PY, "echo"],
            input_text="hello\n",
            timeout_seconds=2.0,
            memory_limit_bytes=100 * 1024 * 1024,
            poll_interval=0.01,
        )
    results["psutil_monitor_ms"] = ((time.monotonic() - t0) / iterations) * 1000

    return results


def test_memory_kill() -> dict[str, bool]:
    # Memory limit 30 MB; worker attempts to allocate 100 MB+
    results = {}

    # Job Object
    with WindowsJobObject(memory_limit_bytes=30 * 1024 * 1024) as job:
        proc = subprocess.Popen(
            [sys.executable, WORKER_PY, "leak"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        job.assign_process(int(proc._handle))
        stdout, stderr = proc.communicate(timeout=5)
        # Process should receive MemoryError (exit 42) or fail commit
        results["job_object_caught_memory"] = "MEMORY_ERROR" in stdout or proc.returncode != 0

    # psutil monitor
    res = run_monitored_process(
        [sys.executable, WORKER_PY, "leak"],
        timeout_seconds=5.0,
        memory_limit_bytes=40 * 1024 * 1024,
        poll_interval=0.01,
    )
    results["psutil_caught_memory"] = res.killed_by_memory

    return results


def test_timeout_and_tree_kill() -> dict[str, bool]:
    results = {}

    # psutil timeout on hang
    res_hang = run_monitored_process(
        [sys.executable, WORKER_PY, "hang"],
        timeout_seconds=0.5,
        poll_interval=0.02,
    )
    results["psutil_killed_hang"] = res_hang.killed_by_timeout

    # psutil timeout on process tree
    res_tree = run_monitored_process(
        [sys.executable, WORKER_PY, "tree"],
        timeout_seconds=0.5,
        poll_interval=0.02,
    )
    results["psutil_killed_tree"] = res_tree.killed_by_timeout

    return results


def main() -> None:
    print("=== Spike S3: Subprocess Isolation Benchmark ===")
    overhead = measure_startup_overhead()
    print("Startup overhead per run:")
    for k, v in overhead.items():
        print(f"  {k}: {v:.1f} ms")

    mem_results = test_memory_kill()
    print("Memory limit enforcement:")
    for k, v in mem_results.items():
        print(f"  {k}: {v}")

    tree_results = test_timeout_and_tree_kill()
    print("Timeout and tree kill:")
    for k, v in tree_results.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
