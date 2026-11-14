# Mirrors scripts/*.ps1 one-to-one (a test enforces it). PowerShell scripts are primary.
.PHONY: install check test demo serve audit

install:
	uv sync --frozen

check:
	uv run --frozen python scripts/run_check.py

test:
	uv run --frozen pytest

demo:
	uv run --frozen search-engine demo

serve:
	@echo "serve: the API server is introduced in Step 22 and is not available yet."
	@exit 2

audit:
	uv run --frozen bandit -q -r src -ll
	uv run --frozen pip-licenses --format=markdown
