"""Structured JSON logging with redaction (rule S8).

Never logged: cookies, authorization headers, API keys, secrets, and raw query text
(unless ``log_raw_queries`` is explicitly enabled).
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

REDACTED = "[redacted]"
LOG_FILE_NAME = "search-engine.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 9  # the live file plus 9 backups = 10 files of 5 MB

run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_SENSITIVE_KEYS = frozenset(
    {
        "cookie",
        "cookies",
        "set-cookie",
        "set_cookie",
        "authorization",
        "proxy-authorization",
        "api_key",
        "apikey",
        "x-api-key",
        "x_api_key",
        "api_key_hash",
        "password",
        "secret",
        "token",
        "access_token",
    }
)
_QUERY_KEYS = frozenset({"query", "q", "raw_query", "search_query"})

_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(cookie|set-cookie|authorization|proxy-authorization|x-api-key)\b\s*[:=]\s*[^\r\n]+"), r"\1: " + REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    (re.compile(r"\bse_[A-Za-z0-9_-]{16,}"), REDACTED),
)

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))
) | {"message", "asctime", "taskName"}


def redact_text(text: str) -> str:
    """Scrub credentials that appear inside free text."""
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(key: str, value: Any, *, log_raw_queries: bool = False) -> Any:
    """Redact by key name, recursing into mappings and sequences."""
    lowered = key.lower()
    if lowered in _SENSITIVE_KEYS:
        return REDACTED
    if lowered in _QUERY_KEYS and not log_raw_queries:
        return REDACTED
    if isinstance(value, dict):
        return {
            str(k): redact_value(str(k), v, log_raw_queries=log_raw_queries)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_value(key, item, log_raw_queries=log_raw_queries) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, int | float | bool) or value is None:
        return value
    return redact_text(str(value))


class JsonFormatter(logging.Formatter):
    def __init__(self, *, log_raw_queries: bool = False) -> None:
        super().__init__()
        self._log_raw_queries = log_raw_queries

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "run_id": run_id_var.get(),
            "request_id": request_id_var.get(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = redact_value(key, value, log_raw_queries=self._log_raw_queries)
        if record.exc_info and record.exc_info[1] is not None:
            payload["exc_type"] = type(record.exc_info[1]).__name__
            payload["exc_message"] = redact_text(str(record.exc_info[1]))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    *,
    level: str = "INFO",
    log_dir: Path | None = None,
    stream: IO[str] | None = None,
    log_raw_queries: bool = False,
) -> logging.Logger:
    """Configure the ``search_engine`` logger (idempotent: replaces its handlers)."""
    logger = logging.getLogger("search_engine")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    logger.propagate = False
    formatter = JsonFormatter(log_raw_queries=log_raw_queries)
    handlers: list[logging.Handler] = [logging.StreamHandler(stream or sys.stderr)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / LOG_FILE_NAME,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
