from __future__ import annotations

import json
from pathlib import Path

import pytest

from search_engine import cli
from search_engine.config import (
    ENV_PREFIX,
    HARD_MAX_PAGES,
    Settings,
    describe,
    field_sources,
    load_settings,
)
from search_engine.errors import ConfigError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.upper().startswith(ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)


def write_toml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_bounded_and_safe() -> None:
    s = load_settings()
    assert not s.real_network_enabled
    assert not s.allow_loopback_for_tests
    assert not s.news_images_enabled
    assert not any(
        (s.feature_images, s.feature_entities, s.feature_autocomplete, s.feature_remote_test)
    )
    assert s.max_pages <= HARD_MAX_PAGES
    assert s.news_max_items == 500 and s.news_retention_days == 14
    assert s.per_host_delay_seconds >= 1.0
    assert s.per_host_concurrency == 1
    assert s.max_html_bytes == 2 * 1024 * 1024
    assert s.max_pdf_bytes == 5 * 1024 * 1024


def test_every_numeric_limit_is_finite_and_positive_so_nothing_is_unbounded() -> None:
    s = load_settings(overrides={"development_mode": "true"})
    for name in Settings.model_fields:
        value = getattr(s, name)
        if name.startswith(("max_", "news_max", "entity_max", "image_max")) and isinstance(
            value, int | float
        ):
            assert 0 < value < float("inf"), name


def test_user_agent_includes_policy_url() -> None:
    s = load_settings(overrides={"policy_url": "https://example.org/bot"})
    assert s.user_agent.startswith("IndependentSearchBot/")
    assert s.user_agent.endswith("(+https://example.org/bot)")
    assert "(+" not in load_settings().user_agent


def test_environment_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_ENGINE_MAX_PAGES", "42")
    monkeypatch.setenv("SEARCH_ENGINE_ALLOWLIST_MODE", "false")
    monkeypatch.setenv("SEARCH_ENGINE_ALLOWED_CONTENT_TYPES", "text/html, TEXT/plain")
    s = load_settings()
    assert s.max_pages == 42
    assert s.allowlist_mode is False
    assert s.allowed_content_types == ("text/html", "text/plain")


def test_precedence_default_then_file_then_env_then_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_toml(tmp_path, "max_pages = 100\nmax_depth = 2\nlog_level = 'DEBUG'\n")
    assert load_settings(config).max_pages == 100
    monkeypatch.setenv("SEARCH_ENGINE_MAX_PAGES", "200")
    assert load_settings(config).max_pages == 200
    assert load_settings(config, {"max_pages": "300"}).max_pages == 300
    s = load_settings(config, {"max_pages": "300"})
    assert s.max_depth == 2  # file value survives where nothing overrides it
    assert s.max_html_bytes == 2 * 1024 * 1024  # default survives


def test_field_sources_report_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = write_toml(tmp_path, "max_depth = 2\nmax_pages = 100\n")
    monkeypatch.setenv("SEARCH_ENGINE_MAX_PAGES", "200")
    sources = field_sources(config, {"log_level": "WARNING"})
    assert sources["log_level"] == "cli"
    assert sources["max_pages"] == "env"
    assert sources["max_depth"] == "file"
    assert sources["max_html_bytes"] == "default"


def test_real_network_and_loopback_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigError, match="cannot both be true"):
        load_settings(
            overrides={"real_network_enabled": "true", "allow_loopback_for_tests": "true"}
        )
    monkeypatch.setenv("SEARCH_ENGINE_REAL_NETWORK_ENABLED", "true")
    monkeypatch.setenv("SEARCH_ENGINE_ALLOW_LOOPBACK_FOR_TESTS", "true")
    with pytest.raises(ConfigError, match="cannot both be true"):
        load_settings()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_pages", "1001"),
        ("max_depth", "11"),
        ("news_max_items", "501"),
        ("news_retention_days", "15"),
        ("image_max_files", "1501"),
        ("entity_max", "2001"),
        ("max_html_bytes", str(3 * 1024 * 1024)),
        ("max_pdf_bytes", str(6 * 1024 * 1024)),
        ("max_redirects", "6"),
        ("per_host_concurrency", "2"),
        ("max_crawl_delay_seconds", "31"),
        ("news_summary_max_chars", "301"),
    ],
)
def test_hard_ceilings_cannot_be_raised(
    key: str, value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(f"SEARCH_ENGINE_{key.upper()}", value)
    with pytest.raises(ConfigError, match=key):
        load_settings()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_pages", "0"),
        ("max_pages", "many"),
        ("connect_timeout_seconds", "-1"),
        ("per_host_delay_seconds", "0.5"),
        ("news_poll_interval_minutes", "5"),
        ("log_level", "LOUD"),
        ("allowlist_mode", "maybe"),
        ("unknown_setting", "1"),
    ],
)
def test_invalid_values_fail_clearly(key: str, value: str) -> None:
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_settings(overrides={key: value})


def test_unreleased_feature_flags_are_rejected() -> None:
    for flag in (
        "feature_images",
        "feature_entities",
        "feature_autocomplete",
        "feature_remote_test",
    ):
        with pytest.raises(ConfigError, match=flag):
            load_settings(overrides={flag: "true"})
    with pytest.raises(ConfigError, match="news_images_enabled"):
        load_settings(overrides={"news_images_enabled": "true"})


def test_cross_field_rules() -> None:
    with pytest.raises(ConfigError, match="synopsis_min_chars"):
        load_settings(overrides={"synopsis_min_chars": "400"})
    with pytest.raises(ConfigError, match="backoff_base_seconds"):
        load_settings(overrides={"backoff_base_seconds": "100", "backoff_max_seconds": "10"})


def test_bad_config_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "missing.toml")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_settings(write_toml(tmp_path, "max_pages = = 1"))
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_settings(write_toml(tmp_path, "nope = 1"))


def test_describe_redacts_secrets() -> None:
    s = load_settings(overrides={"api_key_hash": "abc123hash"})
    rows = {name: value for name, value, _ in describe(s, field_sources(None, None))}
    assert rows["api_key_hash"] == "[redacted]"
    assert "abc123hash" not in repr(s)
    assert "abc123hash" not in str(describe(s, {}))
    assert dict((n, v) for n, v, _ in describe(load_settings(), {}))["api_key_hash"] == "(unset)"


def test_config_show_cli_marks_sources_and_hides_secrets(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEARCH_ENGINE_MAX_PAGES", "77")
    code = cli.main(
        ["--set", "api_key_hash=topsecretvalue", "--set", "max_depth=1", "config", "show"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "topsecretvalue" not in out
    assert "max_pages" in out and "= 77  [env]" in out
    assert "= 1  [cli]" in out
    assert "[default]" in out


def test_config_show_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config", "show", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {"name", "value", "source"} <= set(rows[0])


def test_cli_reports_invalid_config_without_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--set", "max_pages=5000", "config", "show"]) == 3
    captured = capsys.readouterr()
    assert "config_error" in captured.err
    assert "Traceback" not in captured.err
    assert cli.main(["--set", "novalue", "config", "show"]) == 3
