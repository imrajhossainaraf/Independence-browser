"""Subprocess isolation and execution wrapper with memory and time bounds."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass(frozen=True)
class IsolatedProcessResult:
    """The outcome of running an isolated subprocess."""

    returncode: int
    stdout: str
    stderr: str
    killed_by_timeout: bool = False
    killed_by_memory: bool = False
    memory_limit_unavailable: bool = False
    duration_seconds: float = 0.0


def _kill_process_tree(pid: int) -> None:
    """Recursively kill a process and all its child processes."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    for child in parent.children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass

    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass


def _setup_windows_job_object(memory_limit_bytes: int | None) -> Any:
    """Create a Windows Job Object with memory and kill-on-close limits."""
    if sys.platform != "win32":
        return None

    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryLimit", ctypes.c_size_t),
            ("PeakJobMemoryLimit", ctypes.c_size_t),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_bytes and memory_limit_bytes > 0:
            flags |= 0x00000200  # JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_limit_bytes

        info.BasicLimitInformation.LimitFlags = flags
        res = kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not res:
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:  # noqa: BLE001
        return None


def run_isolated_process(
    cmd: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: float = 10.0,
    memory_limit_bytes: int | None = 512 * 1024 * 1024,
    poll_interval: float = 0.02,
) -> IsolatedProcessResult:
    """Execute a subprocess inside a bounded sandbox with memory and timeout enforcement.

    Enforces:
    1. Spawn semantics.
    2. Whole-process-tree kill on exit or failure.
    3. Windows Job Object memory limit (commit limit) and kill-on-job-close.
    4. psutil RSS monitoring thread across the process tree.
    5. Hard wall-clock timeout.
    """
    job_handle = _setup_windows_job_object(memory_limit_bytes)
    memory_unavailable = (
        memory_limit_bytes is not None and job_handle is None and sys.platform != "win32"
    )

    start_time = time.monotonic()
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if job_handle and sys.platform == "win32":
        try:
            import ctypes

            handle = getattr(proc, "_handle", None)
            if handle is not None:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.AssignProcessToJobObject(job_handle, int(handle))
        except Exception:  # noqa: BLE001, S110
            pass

    killed_by_timeout = False
    killed_by_memory = False
    stop_event = threading.Event()

    def monitor() -> None:
        nonlocal killed_by_timeout, killed_by_memory
        try:
            ps_proc = psutil.Process(proc.pid)
        except psutil.NoSuchProcess:
            return

        while not stop_event.is_set():
            if time.monotonic() - start_time > timeout_seconds:
                killed_by_timeout = True
                _kill_process_tree(proc.pid)
                break

            try:
                total_rss = ps_proc.memory_info().rss
                for child in ps_proc.children(recursive=True):
                    total_rss += child.memory_info().rss

                if memory_limit_bytes and total_rss > memory_limit_bytes:
                    killed_by_memory = True
                    _kill_process_tree(proc.pid)
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break

            time.sleep(poll_interval)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()

    try:
        stdout, stderr = proc.communicate(input=input_text)
    finally:
        stop_event.set()
        monitor_thread.join(timeout=1.0)
        _kill_process_tree(proc.pid)

        if job_handle and sys.platform == "win32":
            try:
                import ctypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle(job_handle)
            except Exception:  # noqa: BLE001, S110
                pass

    duration = time.monotonic() - start_time
    return IsolatedProcessResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        killed_by_timeout=killed_by_timeout,
        killed_by_memory=killed_by_memory,
        memory_limit_unavailable=memory_unavailable,
        duration_seconds=duration,
    )
