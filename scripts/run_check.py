"""Run the project check: ruff format check, ruff lint, mypy, pytest (with coverage).

Single source of truth for scripts/check.ps1, the Makefile, and the Dockerfile.
Stops at the first failing stage and exits nonzero.
"""

from __future__ import annotations

import subprocess
import sys

STAGES: list[tuple[str, list[str]]] = [
    ("ruff format check", ["ruff", "format", "--check", "."]),
    ("ruff lint", ["ruff", "check", "."]),
    ("mypy", ["mypy"]),
    ("pytest", ["coverage", "run", "-m", "pytest"]),
    ("coverage report", ["coverage", "report"]),
]


def main() -> int:
    for name, command in STAGES:
        print(f"==> {name}", flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            print(f"FAILED: {name} (exit {result.returncode})", flush=True)
            return result.returncode
    print("check: all stages passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
