"""API security, key generation, and authentication helpers (D15)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from search_engine.config import Settings


def generate_api_key() -> str:
    """Generate a high-entropy URL-safe API key with 'se_' prefix."""
    token = secrets.token_urlsafe(32)
    return f"se_{token}"


def hash_api_key(raw_key: str) -> str:
    """Compute SHA-256 hex digest of an API key."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def is_loopback_host(host: str) -> bool:
    """Check if host string is a recognized loopback address."""
    h = host.strip().lower()
    return h in {"127.0.0.1", "localhost", "::1"}


def validate_bearer_token(
    raw_token: str,
    settings: Settings,
    conn: sqlite3.Connection,
) -> bool:
    """Validate bearer token against config hash or database api_keys in constant time."""
    if not raw_token or not raw_token.strip():
        return False

    candidate_hash = hash_api_key(raw_token.strip())

    # 1. Check settings api_key_hash if configured
    if settings.api_key_hash is not None:
        expected = settings.api_key_hash.get_secret_value().strip()
        if hmac.compare_digest(candidate_hash, expected):
            return True

    # 2. Check database api_keys table
    try:
        rows = conn.execute("SELECT key_hash FROM api_keys WHERE status = 'active'").fetchall()
        for row in rows:
            expected_hash = str(row[0])
            if hmac.compare_digest(candidate_hash, expected_hash):
                return True
    except sqlite3.OperationalError:
        # Table might not exist yet if unmigrated
        pass

    return False


def has_configured_keys(settings: Settings, conn: sqlite3.Connection) -> bool:
    """Check whether any active API key exists in settings or database."""
    if settings.api_key_hash is not None:
        return True
    try:
        row = conn.execute("SELECT count(*) FROM api_keys WHERE status = 'active'").fetchone()
        return bool(row and row[0] > 0)
    except sqlite3.OperationalError:
        return False


def verify_server_startup_safety(settings: Settings, conn: sqlite3.Connection) -> None:
    """Refuse server startup on non-loopback bind without an API key per D15."""
    if not is_loopback_host(settings.server_host):
        if not has_configured_keys(settings, conn):
            raise RuntimeError(
                f"Startup refused: binding to non-loopback address '{settings.server_host}' "
                "requires an API key (api_key_hash in settings or active key in database). "
                "See docs/server-security.md for requirements."
            )
