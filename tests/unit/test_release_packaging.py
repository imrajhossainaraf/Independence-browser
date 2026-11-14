"""Unit tests for Step 30 release packaging, version consistency, and documentation completeness."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import search_engine
from search_engine.config import Settings


def test_version_consistency() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    pyproject_path = root / "pyproject.toml"
    assert pyproject_path.exists()

    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    pyproject_version = data["project"]["version"]
    pkg_version = search_engine.__version__

    assert pyproject_version == "0.1.0", (
        f"pyproject.toml version is {pyproject_version}, expected 0.1.0"
    )
    assert pkg_version == "0.1.0", f"search_engine.__version__ is {pkg_version}, expected 0.1.0"


def test_required_release_documentation_exists() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    docs_dir = root / "docs"

    required_docs = [
        "architecture.md",
        "known-limitations.md",
        "release-notes-v0.1.0.md",
        "configuration.md",
        "PREFLIGHT.md",
        "crawler-policy.md",
        "server-security.md",
        "operations.md",
        "DECISIONS.md",
        "PROGRESS.md",
    ]

    for doc in required_docs:
        doc_path = docs_dir / doc
        assert doc_path.exists(), f"Missing required documentation: {doc}"
        assert doc_path.stat().st_size > 200, f"Document is unexpectedly small: {doc}"


def test_configuration_reference_covers_all_settings() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    config_doc_path = root / "docs" / "configuration.md"
    assert config_doc_path.exists()

    content = config_doc_path.read_text(encoding="utf-8")

    # Every setting in Settings.model_fields should be mentioned in docs/configuration.md
    for field_name in Settings.model_fields:
        pattern = rf"`{re.escape(field_name)}`"
        assert re.search(pattern, content), (
            f"Setting '{field_name}' is not documented in docs/configuration.md"
        )


def test_makefile_and_scripts_parity() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    release_script = root / "scripts" / "release-check.ps1"
    assert release_script.exists(), "scripts/release-check.ps1 is missing"

    makefile = root / "Makefile"
    assert makefile.exists()
    makefile_text = makefile.read_text(encoding="utf-8")

    assert "release-check:" in makefile_text, "Makefile is missing 'release-check:' target"
    assert "daily-use:" in makefile_text, "Makefile is missing 'daily-use:' target"


def test_readme_contains_v010_and_quickstart() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    readme_path = root / "README.md"
    assert readme_path.exists()
    readme_text = readme_path.read_text(encoding="utf-8")

    assert "v0.1.0" in readme_text
    assert "scripts/check.ps1" in readme_text or "scripts\\check.ps1" in readme_text
    assert "scripts/serve.ps1" in readme_text or "scripts\\serve.ps1" in readme_text
