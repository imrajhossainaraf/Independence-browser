"""Network and transport errors.

All network-layer exceptions inherit from NetworkError.
"""

from __future__ import annotations


class NetworkError(Exception):
    """Base class for all network errors."""

    code = "network_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message


class ResolutionError(NetworkError):
    """DNS resolution failed, timed out, or returned no usable records."""

    code = "resolution_failed"


class InvalidAddressError(NetworkError):
    """An IP address violates the public-only IP policy (or loopback policy)."""

    code = "invalid_address"


class TransportError(NetworkError):
    """HTTP transport failure."""

    code = "transport_error"


class ConnectError(TransportError):
    """Connection setup failed (e.g. refused or certificate mismatch)."""

    code = "connect_error"


class TimeoutError(TransportError):
    """Network operation timed out."""

    code = "timeout"


class ConnectTimeout(TimeoutError):
    """Connection establishment timed out."""

    code = "connect_timeout"


class ReadTimeout(TimeoutError):
    """Reading response data timed out."""

    code = "read_timeout"


class ResponseTooLarge(TransportError):
    """Response payload exceeded the configured byte limit while streaming."""

    code = "response_too_large"
