# Decisions (ADR log)

Template for each entry: **ADR-NNN, date, status.** Context. Decision. Alternatives. Consequences.

## ADR-001, 2026-11-04, accepted: Python 3.12 via uv
Context: the machine has Python 3.14.7; wheels for planned libraries may lag. Decision: pin Python 3.12 with `uv` (human approved). Alternatives: use 3.14. Consequences: `.python-version` and the Docker image use 3.12.

## ADR-002, 2026-11-04, accepted: Docker added, GitHub Actions deferred
Context: the human asked for Docker and CI/CD; BUILD_PLAN lists CI as optional and has no Docker. Decision: add a Dockerfile (`check` and `runtime` stages) in Step 1; skip GitHub Actions for now (human chose Docker only). Alternatives: Actions on windows-latest and ubuntu-latest. Consequences: the containerized check is the repeatable gate; Actions can be added later without code changes.

## ADR-003, 2026-11-04, accepted: build backend and dev tooling
Context: a src-layout package needs a build backend. Decision: `hatchling` (MIT) as backend; dev tools as listed in BUILD_PLAN Section 4 in a `dev` dependency group; no runtime dependencies yet. Consequences: each runtime dependency gets its own ADR when introduced.

## ADR-004, 2026-11-04, accepted: one check runner
Context: scripts and Makefile must not drift. Decision: `scripts/run_check.py` holds the stage list; `check.ps1`, `make check`, and the Docker `check` stage all call it. Consequences: changing the check means editing one file.

## ADR-005, 2026-11-04, accepted: waived per-step approval stops
Context: the human said to deliver the full finished project and approved all permissions. Decision: continue step by step with a commit and tag per step but without stopping for `approved: step N`. Mandatory stops from BUILD_INSTRUCTIONS Section 2 rule 5 still apply (real internet, source choice, legal, off-list dependencies, thresholds). Consequences: work halts at Step 29 until the human provides the Phase 7 prerequisites.
