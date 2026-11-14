"""Validated configuration. Every limit lives here; later components read this one object.

Precedence (low to high): defaults, config file (TOML), environment (SEARCH_ENGINE_*), CLI flags.
Hard ceilings are enforced by field constraints, so no source can raise them.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    SettingsError,
    TomlConfigSettingsSource,
)

from search_engine import __version__
from search_engine.errors import ConfigError

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

ENV_PREFIX = "SEARCH_ENGINE_"

# Hard ceilings from BUILD_PLAN Sections 3, 5 and 9. Configuration can lower them, never raise them.
HARD_MAX_PAGES = 1_000
HARD_MAX_DEPTH = 10
HARD_NEWS_ITEMS = 500
HARD_NEWS_RETENTION_DAYS = 14
HARD_IMAGE_FILES = 1_500
HARD_IMAGE_BYTES = 150 * MIB
HARD_ENTITIES = 2_000
HARD_HTML_BYTES = 2 * MIB
HARD_PDF_BYTES = 5 * MIB
HARD_SITEMAP_BYTES = 5 * MIB
HARD_IMAGE_ENCODED_BYTES = 2 * MIB
HARD_GLOBAL_CONCURRENCY = 16
HARD_STORAGE_BYTES = 2 * GIB
HARD_RUNTIME_SECONDS = 6 * 3600

# Flags whose steps are not part of Release A. They may only become true after their step passes.
_UNAVAILABLE_FLAGS = (
    "feature_images",
    "feature_entities",
    "feature_autocomplete",
    "feature_remote_test",
)

_DEFAULT_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/pdf",
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
)


class Settings(BaseSettings):
    """The single validated configuration object."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        frozen=True,
        extra="forbid",
        validate_default=True,
    )

    # Identity
    user_agent_token: str = Field(default="IndependentSearchBot", min_length=1, max_length=64)
    policy_url: str = ""

    # Input files
    seeds_file: Path = Path("data/seeds.txt")
    news_sources_file: Path = Path("data/news_sources.txt")
    entities_seed_file: Path = Path("data/entities_seed.txt")
    allowlist_mode: bool = True

    # Crawl scope
    max_pages: int = Field(default=500, ge=1, le=HARD_MAX_PAGES)
    max_depth: int = Field(default=3, ge=0, le=HARD_MAX_DEPTH)
    max_pages_per_host: int = Field(default=200, ge=1, le=HARD_MAX_PAGES)
    recrawl_after_days: int = Field(default=7, ge=1, le=365)

    # Byte limits after decoding (D9)
    max_html_bytes: int = Field(default=2 * MIB, ge=1, le=HARD_HTML_BYTES)
    max_text_bytes: int = Field(default=2 * MIB, ge=1, le=HARD_HTML_BYTES)
    max_feed_bytes: int = Field(default=2 * MIB, ge=1, le=HARD_HTML_BYTES)
    max_pdf_bytes: int = Field(default=5 * MIB, ge=1, le=HARD_PDF_BYTES)
    max_sitemap_bytes: int = Field(default=5 * MIB, ge=1, le=HARD_SITEMAP_BYTES)
    max_image_bytes: int = Field(default=2 * MIB, ge=1, le=HARD_IMAGE_ENCODED_BYTES)
    max_redirects: int = Field(default=5, ge=0, le=5)
    max_storage_bytes: int = Field(default=1 * GIB, ge=1, le=HARD_STORAGE_BYTES)
    max_runtime_seconds: int = Field(default=3600, ge=1, le=HARD_RUNTIME_SECONDS)
    min_free_disk_bytes: int = Field(default=1 * GIB, ge=0)

    # Timeouts
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    dns_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    # Politeness and concurrency (S14)
    per_host_delay_seconds: float = Field(default=1.0, ge=1.0, le=60)
    max_crawl_delay_seconds: float = Field(default=30.0, ge=1.0, le=30.0)
    per_host_concurrency: int = Field(default=1, ge=1, le=1)
    global_concurrency: int = Field(default=8, ge=1, le=HARD_GLOBAL_CONCURRENCY)
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_base_seconds: float = Field(default=2.0, gt=0, le=60)
    backoff_max_seconds: float = Field(default=60.0, gt=0, le=3600)

    allowed_content_types: Annotated[tuple[str, ...], NoDecode] = _DEFAULT_CONTENT_TYPES

    # Paths
    db_path: Path = Path("data/index/search.db")
    raw_dir: Path = Path("data/raw")
    image_dir: Path = Path("data/images")
    log_dir: Path = Path("data/logs")

    # Modes and network gates
    development_mode: bool = False
    real_network_enabled: bool = False
    allow_loopback_for_tests: bool = False
    offline_mode: bool = False

    # Logging
    log_level: str = "INFO"
    log_raw_queries: bool = False

    # News (D16)
    news_max_items: int = Field(default=500, ge=1, le=HARD_NEWS_ITEMS)
    news_retention_days: int = Field(default=14, ge=1, le=HARD_NEWS_RETENTION_DAYS)
    news_poll_interval_minutes: int = Field(default=30, ge=15, le=1440)
    news_summary_max_chars: int = Field(default=300, ge=1, le=300)
    news_images_enabled: bool = False
    news_stale_hours: int = Field(default=24, ge=1)

    # Images and entities (Release B caps, kept so the ceilings exist from the start)
    image_max_files: int = Field(default=1500, ge=0, le=HARD_IMAGE_FILES)
    image_max_total_bytes: int = Field(default=150 * MIB, ge=0, le=HARD_IMAGE_BYTES)
    entity_max: int = Field(default=2000, ge=0, le=HARD_ENTITIES)

    # Panels and synopsis (placeholders, tuned on dev queries only in Step 27)
    synopsis_min_chars: int = Field(default=40, ge=1)
    synopsis_max_chars: int = Field(default=300, ge=1)
    synopsis_min_coverage: float = Field(default=0.6, ge=0, le=1)
    synopsis_min_doc_score: float = Field(default=0.0, ge=0)
    preview_lead_chars: int = Field(default=200, ge=1, le=1000)
    recent_news_days: int = Field(default=7, ge=1, le=14)
    recent_news_max: int = Field(default=3, ge=0, le=3)

    # Feature flags (Release tiers)
    feature_images: bool = False
    feature_entities: bool = False
    feature_autocomplete: bool = False
    feature_remote_test: bool = False

    # Server and API settings (Step 22)
    server_host: str = Field(default="127.0.0.1")
    server_port: int = Field(default=8000, ge=1, le=65535)
    api_auth_required: bool = False
    api_allowed_hosts: tuple[str, ...] = (
        "127.0.0.1",
        "localhost",
        "127.0.0.1:8000",
        "localhost:8000",
        "testserver",
    )
    api_cors_enabled: bool = False
    api_cors_origins: tuple[str, ...] = ()
    api_rate_limit_rpm: int = Field(default=60, ge=1, le=1000)
    api_request_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30.0)
    log_query_text: bool = False

    # Secrets are never committed; shown redacted.
    api_key_hash: SecretStr | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: tuple[PydanticBaseSettingsSource, ...] = (init_settings, env_settings)
        if settings_cls.model_config.get("toml_file"):
            sources += (TomlConfigSettingsSource(settings_cls),)
        return sources

    @field_validator("allowed_content_types", mode="before")
    @classmethod
    def _split_content_types(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip().lower() for part in value.split(",") if part.strip())
        return value

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _validate_cors_origins(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            parts = tuple(part.strip() for part in value.split(",") if part.strip())
        elif isinstance(value, (list, tuple, set)):
            parts = tuple(str(p).strip() for p in value if str(p).strip())
        else:
            parts = ()
        if "*" in parts:
            raise ValueError("wildcard origin '*' is forbidden; exact origins required")
        return parts

    @field_validator("api_allowed_hosts", mode="before")
    @classmethod
    def _validate_allowed_hosts(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, (list, tuple, set)):
            return tuple(str(p).strip() for p in value if str(p).strip())
        return ("127.0.0.1", "localhost", "127.0.0.1:8000", "localhost:8000", "testserver")

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError("must be DEBUG, INFO, WARNING, or ERROR")
        return upper

    @model_validator(mode="after")
    def _cross_checks(self) -> Settings:
        if self.real_network_enabled and self.allow_loopback_for_tests:
            raise ValueError(
                "real_network_enabled and allow_loopback_for_tests cannot both be true"
            )
        if self.offline_mode and self.real_network_enabled:
            raise ValueError("offline_mode and real_network_enabled cannot both be true")
        for name in _UNAVAILABLE_FLAGS:
            if getattr(self, name):
                raise ValueError(f"{name} cannot be enabled: its step is not part of this release")
        if self.news_images_enabled and not self.feature_images:
            raise ValueError("news_images_enabled requires feature_images, which is unavailable")
        if self.synopsis_min_chars > self.synopsis_max_chars:
            raise ValueError("synopsis_min_chars must not exceed synopsis_max_chars")
        if self.backoff_base_seconds > self.backoff_max_seconds:
            raise ValueError("backoff_base_seconds must not exceed backoff_max_seconds")
        return self

    @property
    def user_agent(self) -> str:
        agent = f"{self.user_agent_token}/{__version__}"
        return f"{agent} (+{self.policy_url})" if self.policy_url else agent


def _format_errors(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "settings"
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


def _read_toml(config_file: Path) -> dict[str, Any]:
    try:
        with config_file.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_file.name}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file is not valid TOML: {exc}") from exc


def load_settings(
    config_file: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Build the effective settings. Raises ConfigError with a clear message when invalid."""
    cls: type[Settings] = Settings
    if config_file is not None:
        _read_toml(config_file)  # fail early with a precise message

        class FileSettings(Settings):
            model_config = SettingsConfigDict(toml_file=str(config_file))

        cls = FileSettings
    try:
        return cls(**dict(overrides or {}))
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration: {_format_errors(exc)}") from exc
    except SettingsError as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc


def field_sources(
    config_file: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Where each effective value came from: cli, env, file, or default."""
    env = {key.upper() for key in (os.environ if environ is None else environ)}
    file_keys = set(_read_toml(config_file)) if config_file is not None else set()
    given = set(overrides or {})
    sources: dict[str, str] = {}
    for name in Settings.model_fields:
        if name in given:
            sources[name] = "cli"
        elif f"{ENV_PREFIX}{name.upper()}" in env:
            sources[name] = "env"
        elif name in file_keys:
            sources[name] = "file"
        else:
            sources[name] = "default"
    return sources


def _display(value: object) -> str:
    if isinstance(value, SecretStr):
        return "[redacted]"
    if value is None:
        return "(unset)"
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple):
        return ",".join(str(item) for item in value)
    return str(value)


def describe(settings: Settings, sources: Mapping[str, str]) -> list[tuple[str, str, str]]:
    """Rows of (name, display value, source) with secrets redacted."""
    return [
        (name, _display(getattr(settings, name)), sources.get(name, "default"))
        for name in Settings.model_fields
    ]
