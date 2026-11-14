# Mirrors scripts/*.ps1 one-to-one (a test enforces it). PowerShell scripts are primary.
.PHONY: install check test demo eval serve audit news-poll daily-use

install:
	uv sync --frozen

check:
	uv run --frozen python scripts/run_check.py

test:
	uv run --frozen pytest

demo:
	uv run --frozen search-engine demo

eval:
	uv run --frozen search-engine eval --split dev --check-baseline eval/baseline.json

serve:
	uv run --frozen search-engine serve

audit:
	uv run --frozen bandit -q -r src -ll
	uv run --frozen pip-licenses --format=markdown

news-poll:
	uv run --frozen search-engine news poll

daily-use:
	uv run --frozen search-engine daily-use
