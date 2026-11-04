# Independent Search Engine

A self-hosted search engine for one Windows laptop. The engine lives on the server; the web page is a thin client for the read-only `/api/v1`. Status: under construction (see `docs/PROGRESS.md`).

## Setup (Windows PowerShell)

```powershell
# 1. Install uv once: https://docs.astral.sh/uv/  (no administrator rights needed)
# 2. From the repository root:
./scripts/install.ps1     # creates the environment from uv.lock (Python 3.12)
./scripts/check.ps1       # ruff format check, ruff lint, mypy, pytest with coverage
./scripts/demo.ps1        # runs `search-engine health`
```

If the execution policy blocks a script, run it as `powershell -ExecutionPolicy Bypass -File scripts/check.ps1`. The scripts never change your policy.

`make install`, `make check`, and so on do the same on machines with `make`.

## Docker (reproducible check and slim runtime image)

```powershell
docker build --target check -t search-engine:check .      # runs the full check inside the image
docker build --target runtime -t search-engine:dev .
docker run --rm --network none search-engine:dev          # prints health JSON, needs no network
```

## Safety

The crawler cannot reach the real internet until you enable it and create `data/preflight.approved.json` yourself. Tests never use the network. See `CLAUDE.md` for the invariants and `BUILD_PLAN.md` for the full specification.
