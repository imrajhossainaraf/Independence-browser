"""Process isolation and tree-killing using psutil monitoring."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

import psutil


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    killed_by_timeout: bool = False
    killed_by_memory: bool = False
    peak_memory_bytes: int = 0
    duration_seconds: float = 0.0


def kill_process_tree(pid: int) -> None:
    """Recursively kill a process and all its children."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    children = parent.children(recursive=True)
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass

    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass


def run_monitored_process(
    cmd: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: float = 5.0,
    memory_limit_bytes: int | None = None,
    poll_interval: float = 0.02,
) -> ProcessResult:
    """Run a subprocess with hard timeout, psutil tree monitoring, and memory enforcement."""
    start_time = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    killed_by_timeout = False
    killed_by_memory = False
    peak_memory = 0

    stop_monitor = threading.Event()

    def monitor() -> None:
        nonlocal killed_by_timeout, killed_by_memory, peak_memory
        try:
            ps_proc = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            return

        while not stop_monitor.is_set():
            now = time.monotonic()
            if now - start_time > timeout_seconds:
                killed_by_timeout = True
                kill_process_tree(proc.pid)
                break

            try:
                # Sum RSS across process tree
                total_rss = ps_proc.memory_info().rss
                for child in ps_proc.children(recursive=True):
                    total_rss += child.memory_info().rss

                if total_rss > peak_memory:
                    peak_memory = total_rss

                if memory_limit_bytes and total_rss > memory_limit_bytes:
                    killed_by_memory = True
                    kill_process_tree(proc.pid)
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break

            time.sleep(poll_interval)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()

    try:
        stdout, stderr = proc.communicate(input=input_text)
    finally:
        stop_monitor.set()
        monitor_thread.join(timeout=1.0)
        # Ensure process tree is completely cleaned up
        kill_process_tree(proc.pid)

    duration = time.monotonic() - start_time
    return ProcessResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        killed_by_timeout=killed_by_timeout,
        killed_by_memory=killed_by_memory,
        peak_memory_bytes=peak_memory,
        duration_seconds=duration,
    )
