"""Process file lock with stale lock recovery for Windows (Spike S4, decision D16).

Implements:
- OS-level non-blocking file locking (msvcrt on Windows, fcntl on Unix).
- Automatic release on process termination (OS kernel guarantee).
- PID and timestamp metadata stored inside lock file.
- Stale lock detection and safe recovery if a process crashed.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

import psutil

try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]


class LockError(Exception):
    """Raised when file lock operations fail."""


class LockHeldError(LockError):
    """Raised when another active process currently holds the lock."""


class ProcessLock:
    """Exclusive process lock file supporting automatic recovery from killed processes."""

    def __init__(self, lock_file: Path, *, timeout_seconds: float = 0.0) -> None:
        self.lock_file = lock_file
        self.timeout_seconds = timeout_seconds
        self._file: IO[str] | None = None
        self._locked = False

    @property
    def is_locked(self) -> bool:
        return self._locked

    def _try_lock_fd(self, fd: int) -> bool:
        """Attempt non-blocking OS lock on open file descriptor."""
        if msvcrt is not None:
            try:
                # Seek to start
                os.lseek(fd, 0, os.SEEK_SET)
                # LK_NBLCK: Non-blocking lock on 1 byte
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        elif fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                return False
        return True

    def _unlock_fd(self, fd: int) -> None:
        """Release OS lock on file descriptor."""
        if msvcrt is not None:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        elif fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass

    def _is_holder_alive(self) -> bool:
        """Check if recorded holder PID in lock file is currently running."""
        if not self.lock_file.exists():
            return False
        try:
            content = self.lock_file.read_text(encoding="utf-8").strip()
            if not content:
                return False
            data = json.loads(content)
            pid = int(data.get("pid", 0))
            if pid <= 0 or pid == os.getpid():
                return False
            if not psutil.pid_exists(pid):
                return False
            # Check if process is still running and not zombie
            proc = psutil.Process(pid)
            return bool(proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE)
        except (ValueError, OSError, psutil.Error):
            return False

    def acquire(self) -> bool:
        """Acquire lock or raise LockHeldError if held by active process."""
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()

        while True:
            # 1. Open file with read/write access
            try:
                f = open(self.lock_file, "a+", encoding="utf-8")
            except OSError as exc:
                raise LockError(f"Failed opening lock file {self.lock_file}: {exc}") from exc

            # 2. Try OS lock
            if self._try_lock_fd(f.fileno()):
                # OS lock acquired successfully
                self._file = f
                # Write current process metadata
                metadata = {
                    "pid": os.getpid(),
                    "acquired_at": datetime.now(UTC).isoformat(),
                    "host": os.environ.get("COMPUTERNAME", "localhost"),
                }
                f.seek(0)
                f.truncate()
                f.write(json.dumps(metadata) + "\n")
                f.flush()
                self._locked = True
                return True

            # OS lock could not be acquired; another process holds it
            f.close()

            # 3. Check for stale lock (if PID holder died without cleanup)
            if not self._is_holder_alive():
                # Holder is dead! Attempt breaking stale lock by removing or retrying
                try:
                    # On Windows, if process is dead, the OS lock is released immediately.
                    # We can attempt to remove or overwrite the file.
                    self.lock_file.unlink(missing_ok=True)
                except OSError:
                    pass

            elapsed = time.monotonic() - t0
            if elapsed >= self.timeout_seconds:
                raise LockHeldError(f"Lock file {self.lock_file} is held by another active process")
            time.sleep(min(0.1, self.timeout_seconds - elapsed))

    def release(self) -> None:
        """Release OS lock, truncate metadata, and close file."""
        if not self._locked or self._file is None:
            return
        try:
            self._unlock_fd(self._file.fileno())
            self._file.close()
        except OSError:
            pass
        finally:
            self._file = None
            self._locked = False
            try:
                self.lock_file.unlink(missing_ok=True)
            except OSError:
                pass

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
