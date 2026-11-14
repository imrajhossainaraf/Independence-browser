"""Subprocess worker testing script for Spike S3."""

from __future__ import annotations

import sys
import time


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: worker.py <mode>", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "echo":
        # Normal quick task
        line = sys.stdin.readline().strip()
        print(f"ECHO: {line}")
        sys.exit(0)

    elif mode == "hang":
        # Infinite hang / timeout simulation
        while True:
            time.sleep(0.1)

    elif mode == "leak":
        # Allocate memory until killed or MemoryError
        chunks = []
        try:
            while True:
                # 10 MB per chunk
                chunks.append(bytearray(10 * 1024 * 1024))
                time.sleep(0.01)
        except MemoryError:
            print("MEMORY_ERROR", flush=True)
            sys.exit(42)

    elif mode == "tree":
        # Spawns a child process and hangs
        import subprocess

        child = subprocess.Popen([sys.executable, sys.argv[0], "hang"])
        try:
            while True:
                time.sleep(0.1)
        finally:
            child.terminate()

    else:
        print(f"Unknown mode: {mode}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
