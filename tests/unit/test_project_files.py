from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _script_names() -> set[str]:
    return {p.stem for p in (ROOT / "scripts").glob("*.ps1") if not p.stem.startswith("_")}


def _make_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z][a-z0-9-]*):", text, flags=re.MULTILINE))


def test_every_script_has_a_makefile_target_and_back() -> None:
    assert _script_names() == _make_targets()


def test_required_project_files_exist() -> None:
    for name in (
        "CLAUDE.md",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        "Dockerfile",
        ".dockerignore",
        "docs/PROGRESS.md",
        "docs/DECISIONS.md",
    ):
        assert (ROOT / name).is_file(), name


def test_claude_md_is_short() -> None:
    lines = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 150


def test_scripts_never_change_execution_policy_or_need_admin() -> None:
    for script in (ROOT / "scripts").glob("*.ps1"):
        text = script.read_text(encoding="utf-8")
        assert "Set-ExecutionPolicy" not in text
        assert "RunAs" not in text
