from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest

from search_engine.logs import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    REDACTED,
    JsonFormatter,
    redact_text,
    redact_value,
    request_id_var,
    run_id_var,
    setup_logging,
)


def emit(message: str, *, log_raw_queries: bool = False, **extra: object) -> dict[str, object]:
    stream = io.StringIO()
    logger = setup_logging(stream=stream, log_raw_queries=log_raw_queries)
    logger.info(message, extra=extra)
    for handler in logger.handlers:
        handler.flush()
    line = stream.getvalue().strip().splitlines()[-1]
    result: dict[str, object] = json.loads(line)
    return result


def test_output_is_json_with_ids() -> None:
    run_token = run_id_var.set("run-1")
    request_token = request_id_var.set("req-9")
    try:
        record = emit("hello", count=3)
    finally:
        run_id_var.reset(run_token)
        request_id_var.reset(request_token)
    assert record["message"] == "hello"
    assert record["level"] == "INFO"
    assert record["run_id"] == "run-1"
    assert record["request_id"] == "req-9"
    assert record["count"] == 3


@pytest.mark.parametrize(
    "key", ["cookie", "Set-Cookie", "Authorization", "api_key", "X-API-Key", "password", "token"]
)
def test_sensitive_keys_are_redacted(key: str) -> None:
    assert redact_value(key, "super-secret") == REDACTED


def test_nested_structures_are_redacted() -> None:
    value = redact_value("headers", {"Authorization": "Bearer abc", "ok": "yes", "list": [{"cookie": "c"}]})
    assert value["Authorization"] == REDACTED
    assert value["ok"] == "yes"
    assert value["list"][0]["cookie"] == REDACTED


def test_text_patterns_are_scrubbed() -> None:
    assert "abc123" not in redact_text("Authorization: Bearer abc123")
    assert "abc123" not in redact_text("sent header bearer abc123.def to server")
    assert "se_" not in redact_text("key se_AbCdEfGhIjKlMnOpQrStUvWxYz012345 used")
    assert "sessionid" not in redact_text("Cookie: sessionid=xyz; other=1")
    assert redact_text("plain message") == "plain message"


def test_log_record_message_and_extras_never_leak() -> None:
    record = emit(
        "request Authorization: Bearer topsecret",
        headers={"Cookie": "sid=1"},
        api_key="se_AbCdEfGhIjKlMnOpQrStUvWxYz012345",
    )
    dumped = json.dumps(record)
    assert "topsecret" not in dumped
    assert "sid=1" not in dumped
    assert "se_AbCd" not in dumped


def test_raw_query_text_hidden_by_default_and_opt_in() -> None:
    assert emit("search", query="my private words")["query"] == REDACTED
    assert emit("search", log_raw_queries=True, query="my private words")["query"] == "my private words"


def test_exception_logs_type_and_scrubbed_message() -> None:
    stream = io.StringIO()
    logger = setup_logging(stream=stream)
    try:
        raise ValueError("failed with Bearer leaked-token")
    except ValueError:
        logger.exception("boom")
    record = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert record["exc_type"] == "ValueError"
    assert "leaked-token" not in json.dumps(record)


def test_setup_is_idempotent_and_rotates(tmp_path: Path) -> None:
    setup_logging(log_dir=tmp_path)
    logger = setup_logging(log_dir=tmp_path)
    assert len(logger.handlers) == 2
    rotating = [h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert rotating[0].maxBytes == LOG_MAX_BYTES == 5 * 1024 * 1024
    assert rotating[0].backupCount == LOG_BACKUP_COUNT == 9
    logger.info("to file")
    for handler in logger.handlers:
        handler.flush()
        handler.close()
    assert "to file" in (tmp_path / "search-engine.log").read_text(encoding="utf-8")


def test_formatter_handles_unserializable_extras() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None)
    record.thing = object()
    assert json.loads(formatter.format(record))["thing"]
