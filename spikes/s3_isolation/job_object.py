"""Windows Job Object implementation via ctypes for kernel-level process isolation."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

if sys.platform != "win32":
    raise ImportError("JobObject is only supported on Windows")

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JobObjectExtendedLimitInformation = 9


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


class WindowsJobObject:
    """Encapsulates a Win32 Job Object with memory limits and kill-on-close."""

    def __init__(self, memory_limit_bytes: int | None = None) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = self._kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_limit_bytes and memory_limit_bytes > 0:
            flags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_limit_bytes

        info.BasicLimitInformation.LimitFlags = flags
        res = self._kernel32.SetInformationJobObject(
            self.handle,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not res:
            err = ctypes.get_last_error()
            self._kernel32.CloseHandle(self.handle)
            raise OSError(f"SetInformationJobObject failed: {err}")

    def assign_process(self, process_handle: int) -> bool:
        """Assign a process handle to this job object."""
        res = self._kernel32.AssignProcessToJobObject(self.handle, process_handle)
        return bool(res)

    def close(self) -> None:
        """Close the job object handle."""
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = 0

    def __enter__(self) -> WindowsJobObject:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
