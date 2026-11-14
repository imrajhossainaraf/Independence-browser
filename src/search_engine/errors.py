"""Error hierarchy. Every error carries a stable code used in logs, reports, and API errors."""

from __future__ import annotations


class EngineError(Exception):
    """Base class for all expected engine failures."""

    code = "engine_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message


class PolicyDenied(EngineError):
    code = "policy_denied"


class FetchError(EngineError):
    code = "fetch_error"


class InvalidApproval(PolicyDenied):
    code = "invalid_approval"


class RedirectLimitExceeded(FetchError):
    code = "too_many_redirects"


class ResponseTooLargeError(FetchError):
    code = "response_too_large"


class MimeTypeDisallowed(FetchError):
    code = "mime_type_disallowed"


class PayloadRejected(FetchError):
    code = "payload_rejected"


class StorageLimitExceeded(FetchError):
    code = "storage_limit_exceeded"


class ExtractionError(EngineError):
    code = "extraction_error"


class StorageError(EngineError):
    code = "storage_error"


class ConfigError(EngineError):
    code = "config_error"
