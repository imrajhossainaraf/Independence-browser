"""Unit and contract tests for the API-first HTTP server (Step 22)."""

from __future__ import annotations

import gzip
import json
import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from search_engine import __version__
from search_engine.api.app import create_app
from search_engine.api.middleware import RateLimiter
from search_engine.api.security import (
    generate_api_key,
    hash_api_key,
    verify_server_startup_safety,
)
from search_engine.cli import main
from search_engine.config import Settings, load_settings
from search_engine.db import Database
from search_engine.experience.suggest import rebuild_suggestions
from search_engine.indexer.build import upsert_document_index

pytestmark = pytest.mark.loopback


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "test_api.db").open()
    yield db
    db.close()


def _populate_api_corpus(conn: sqlite3.Connection, now: datetime) -> None:
    """Populate test database with news, documents, and index for API testing."""
    now_iso = now.isoformat()
    t_2d_ago = (now - timedelta(days=2)).isoformat()

    # Sources
    conn.execute(
        """
        INSERT INTO news_sources (id, feed_url_key, name, category, poll_interval_minutes,
                                  last_success_at, consecutive_failures)
        VALUES (1, 'https://tech.example.com/feed', 'Tech Daily', 'Tech', 30, ?, 0),
               (2, 'https://space.example.com/feed', 'Space Weekly', 'Space', 45, ?, 4)
        """,
        (now_iso, t_2d_ago),
    )

    # News items
    news1_summary = (
        "Engineers have demonstrated ultra-low power photonic matrix computation "
        "for signal processing."
    )
    news2_summary = (
        "The solar spacecraft has successfully completed its closest approach to the sun."
    )
    conn.execute(
        """
        INSERT INTO news_items (
            id, source_id, link, title, summary, published_at, category, status,
            first_seen_at, updated_at
        )
        VALUES
        ('news-1', 1, 'https://tech.example.com/photon-engine',
         'Photonic Computing Demonstration in Lab',
         ?, ?, 'Tech', 'active', ?, ?),
        ('news-2', 2, 'https://space.example.com/solar-probe',
         'Solar Probe Completes Closest Perihelion',
         ?, ?, 'Space', 'active', ?, ?)
        """,
        (
            news1_summary,
            now_iso,
            now_iso,
            now_iso,
            news2_summary,
            t_2d_ago,
            t_2d_ago,
            t_2d_ago,
        ),
    )

    # Documents
    doc_photon_body = (
        "Photonic computing harnesses photons rather than electrons to compute data.\n\n"
        "Researchers at the institute have demonstrated photonic matrix multipliers. "
        "The system achieves high energy efficiency and millisecond latency.\n\n"
        "Copyright 2026."
    )

    conn.execute(
        "INSERT INTO hosts (id, host, registered_domain, created_at) "
        "VALUES (1, 'tech.example.com', 'example.com', ?)",
        (now_iso,),
    )
    conn.execute(
        """
        INSERT INTO urls (id, url_key, host_id, status, source, discovered_at, updated_at)
        VALUES (1, 'https://tech.example.com/photonics', 1, 'done', 'seed', ?, ?)
        """,
        (now_iso, now_iso),
    )

    cur = conn.execute(
        """
        INSERT INTO documents (
            url_id, doc_type, final_url, canonical_url, title,
            text_gz, word_count, published_at, fetched_at, status, updated_at
        )
        VALUES (1, 'page', 'https://tech.example.com/photonics', 'https://tech.example.com/photonics',
                'Principles of Photonic Computing and Optics',
                ?, 40, ?, ?, 'active', ?)
        """,
        (gzip.compress(doc_photon_body.encode("utf-8")), now_iso, now_iso, now_iso),
    )
    doc_id = int(cur.lastrowid or 1)
    upsert_document_index(conn, doc_id)
    rebuild_suggestions(conn)


def test_openapi_contract_matches_checked_in_file(test_db: Database) -> None:
    """Contract test: running app OpenAPI schema must match docs/openapi.json exactly."""
    settings = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "development_mode": "true",
        },
    )
    app = create_app(test_db, settings=settings)
    generated_schema = app.openapi()

    openapi_file = Path("docs/openapi.json")
    assert openapi_file.is_file(), "docs/openapi.json must exist"
    checked_in_schema = json.loads(openapi_file.read_text(encoding="utf-8"))

    assert generated_schema["info"]["title"] == checked_in_schema["info"]["title"]
    assert generated_schema["info"]["version"] == checked_in_schema["info"]["version"]
    assert set(generated_schema["paths"].keys()) == set(checked_in_schema["paths"].keys())


def test_security_headers_and_request_id(test_db: Database) -> None:
    """D15: Responses must include strict CSP, nosniff, no-referrer, DENY, and X-Request-ID."""
    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    resp = client.get("/api/v1/health")
    assert resp.status_code == 200

    headers = resp.headers
    assert "x-request-id" in headers
    assert len(headers["x-request-id"]) >= 16
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in headers["content-security-policy"]


def test_host_header_allowlist_and_rejection(test_db: Database) -> None:
    """T9: Requests with unapproved Host headers must return 400 Bad Request."""
    settings = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "api_allowed_hosts": "127.0.0.1,localhost,testserver",
        },
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # 1. Approved host
    resp_ok = client.get("/api/v1/health", headers={"host": "testserver"})
    assert resp_ok.status_code == 200

    # 2. Malicious DNS rebinding host
    resp_bad = client.get("/api/v1/health", headers={"host": "malicious-site.attacker.com"})
    assert resp_bad.status_code == 400
    data = resp_bad.json()
    assert data["error"]["code"] == "invalid_host"


def test_cors_behavior_and_wildcard_rejection(test_db: Database) -> None:
    """T10: CORS disabled by default; exact origin only when enabled; wildcard rejected."""
    # 1. Disabled by default
    settings_default = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app_default = create_app(test_db, settings=settings_default)
    client_default = TestClient(app_default)

    resp1 = client_default.get("/api/v1/health", headers={"origin": "http://client.local"})
    assert "access-control-allow-origin" not in resp1.headers

    # 2. Enabled with exact origin
    settings_cors = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "api_cors_enabled": "true",
            "api_cors_origins": "http://allowed-app.local,https://dashboard.local",
            "api_allowed_hosts": "testserver,localhost",
        },
    )
    app_cors = create_app(test_db, settings=settings_cors)
    client_cors = TestClient(app_cors)

    # Matching origin
    resp_match = client_cors.get(
        "/api/v1/health", headers={"origin": "http://allowed-app.local", "host": "testserver"}
    )
    assert resp_match.headers.get("access-control-allow-origin") == "http://allowed-app.local"

    # Non-matching origin
    resp_unmatch = client_cors.get(
        "/api/v1/health", headers={"origin": "http://evil.com", "host": "testserver"}
    )
    assert "access-control-allow-origin" not in resp_unmatch.headers

    # Preflight OPTIONS
    resp_options = client_cors.options(
        "/api/v1/health", headers={"origin": "http://allowed-app.local", "host": "testserver"}
    )
    assert resp_options.status_code == 204
    assert resp_options.headers.get("access-control-allow-origin") == "http://allowed-app.local"

    # Wildcard rejected by validator
    with pytest.raises(ValueError, match="wildcard origin '\\*' is forbidden"):
        Settings(api_cors_origins=("*",))


def test_api_key_authentication_modes(test_db: Database) -> None:
    """D15, T8: API key authentication enforcement, constant time comparison, error format."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)

    with test_db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO api_keys (id, name, key_hash, created_at, status)
            VALUES ('key-1', 'test-client', ?, ?, 'active')
            """,
            (key_hash, now.isoformat()),
        )

    # 1. Server configured with api_auth_required=True
    settings = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "api_auth_required": "true",
        },
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # Missing auth header -> 401
    resp_missing = client.get("/api/v1/health")
    assert resp_missing.status_code == 401
    assert resp_missing.json()["error"]["code"] == "unauthorized"

    # Malformed header -> 401
    resp_malformed = client.get("/api/v1/health", headers={"Authorization": f"Basic {raw_key}"})
    assert resp_malformed.status_code == 401
    assert resp_malformed.json()["error"]["code"] == "unauthorized"

    # Invalid key -> 401
    resp_invalid = client.get("/api/v1/health", headers={"Authorization": "Bearer se_wrongtoken"})
    assert resp_invalid.status_code == 401
    assert resp_invalid.json()["error"]["code"] == "unauthorized"

    # Valid key -> 200 OK
    resp_valid = client.get("/api/v1/health", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp_valid.status_code == 200

    # Revoked key -> 401
    with test_db.transaction() as conn:
        conn.execute("UPDATE api_keys SET status = 'revoked' WHERE id = 'key-1'")

    resp_revoked = client.get("/api/v1/health", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp_revoked.status_code == 401


def test_startup_safety_refuses_non_loopback_without_key(test_db: Database) -> None:
    """T15: Server must refuse to start on non-loopback bind without an API key."""
    settings = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "server_host": "192.168.1.50",  # non-loopback
        },
    )

    with test_db.reader() as conn:
        # No keys configured in settings or database
        with pytest.raises(RuntimeError, match="Startup refused"):
            verify_server_startup_safety(settings, conn)


def test_rate_limiting_returns_429_with_retry_after(test_db: Database) -> None:
    """T8, T11: Rate limiter triggers 429 and Retry-After header when budget exceeded."""
    limiter = RateLimiter(limit_rpm=3, window_seconds=10.0)
    client_id = "test_ip_hash"

    assert limiter.is_allowed(client_id, now=100.0) is True
    assert limiter.is_allowed(client_id, now=101.0) is True
    assert limiter.is_allowed(client_id, now=102.0) is True

    # 4th request within 10s window exceeds 3 rpm
    assert limiter.is_allowed(client_id, now=103.0) is False
    assert limiter.retry_after(client_id, now=103.0) == 7

    # After window advances past first request (100.0 + 10 = 110.1), allows again
    assert limiter.is_allowed(client_id, now=110.1) is True


def test_home_payload_cache_and_etag_304(test_db: Database) -> None:
    """D15, D20: Home endpoint returns ETag, Cache-Control, and 304 on If-None-Match."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_api_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # 1. Initial request -> 200 with ETag
    resp1 = client.get("/api/v1/home")
    assert resp1.status_code == 200
    etag = resp1.headers.get("etag")
    assert etag is not None
    assert resp1.headers.get("cache-control") == "public, max-age=60"
    data1 = resp1.json()
    assert len(data1["latest"]) == 2

    # 2. Conditional request with matching ETag -> 304 Not Modified
    resp2 = client.get("/api/v1/home", headers={"if-none-match": etag})
    assert resp2.status_code == 304
    assert resp2.text == ""

    # 3. Conditional request with mismatched ETag -> 200 with full body
    resp3 = client.get("/api/v1/home", headers={"if-none-match": '"mismatched-etag"'})
    assert resp3.status_code == 200


def test_search_endpoint_full_flow_and_query_validation(test_db: Database) -> None:
    """Search endpoint returns full SearchResponse payload and validates query limits."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_api_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # 1. Matching search
    resp = client.get("/api/v1/search?q=photonic+computing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "photonic computing"
    assert data["total_approx"] >= 1
    assert data["synopsis"] is not None
    assert "photonic" in data["synopsis"]["text"].lower()

    # 2. Overlength query (> 256 chars) -> 400 Bad Request
    long_q = "a" * 260
    resp_long = client.get(f"/api/v1/search?q={long_q}")
    assert resp_long.status_code == 400
    assert resp_long.json()["error"]["code"] == "query_too_long"

    # 3. Empty query -> returns empty result set without error
    resp_empty = client.get("/api/v1/search?q=")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["total_approx"] == 0


def test_feature_flags_gate_suggest_and_images_with_404(test_db: Database) -> None:
    """When feature flags are off, /suggest and /images/{id} endpoints return 404."""
    settings = load_settings(
        None,
        {
            "db_path": str(test_db.path),
            "allow_loopback_for_tests": "true",
            "feature_images": "false",
            "feature_autocomplete": "false",
        },
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    resp_sug = client.get("/api/v1/suggest?q=photo")
    assert resp_sug.status_code == 404
    assert resp_sug.json()["error"]["code"] == "not_found"

    resp_img = client.get("/api/v1/images/img-123")
    assert resp_img.status_code == 404
    assert resp_img.json()["error"]["code"] == "not_found"


def test_document_metadata_endpoint(test_db: Database) -> None:
    """GET /api/v1/documents/{id} returns clean metadata and 404 for unknown IDs."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_api_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # 1. Existing active document
    resp = client.get("/api/v1/documents/1")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["id"] == 1
    assert "tech.example.com" in doc["url"]
    assert doc["title"] == "Principles of Photonic Computing and Optics"
    assert doc["doc_type"] == "page"

    # 2. Non-existent document
    resp_missing = client.get("/api/v1/documents/9999")
    assert resp_missing.status_code == 404
    assert resp_missing.json()["error"]["code"] == "not_found"


def test_health_check_endpoint_reports_status_without_paths(test_db: Database) -> None:
    """GET /api/v1/health reports schema, index, versions, and data ages without leaking paths."""
    now = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
    with test_db.transaction() as conn:
        _populate_api_corpus(conn, now)

    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()

    assert data["api_version"] == __version__
    assert data["schema_version"] in {"0003", "0004"}
    assert data["index_version"] == 1
    assert data["components"]["database"] == "healthy"
    assert data["components"]["index"] == "healthy"
    assert data["data_ages"]["last_poll_at"] is not None

    # Prove no paths leaked in output JSON
    raw_json = json.dumps(data)
    assert "C:\\" not in raw_json
    assert "/Users/" not in raw_json
    assert ".db" not in raw_json


def test_graceful_degradation_matrix_cases(test_db: Database) -> None:
    """Section 7A Graceful Degradation tests: empty news, index missing, database locked."""
    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # 1. Empty news store -> Home payload handles gracefully with 0 data age, search unaffected
    resp_home = client.get("/api/v1/home")
    assert resp_home.status_code == 200
    assert resp_home.json()["latest"] == []
    assert resp_home.json()["top_stories"] == []

    # 2. Missing index table -> returns 503 with index_unavailable code
    with test_db.transaction() as conn:
        conn.execute("DROP TABLE doc_fts")

    resp_search = client.get("/api/v1/search?q=test")
    assert resp_search.status_code == 503
    assert resp_search.json()["error"]["code"] == "index_unavailable"


def test_cli_auth_commands(
    test_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI auth create-key, list-keys, and revoke-key work end-to-end."""
    db_arg = f"--db={test_db.path}"

    # 1. Create key
    ret1 = main(["auth", "create-key", "--name=worker-1", db_arg])
    assert ret1 == 0
    out1 = capsys.readouterr().out
    assert "Created API key 'worker-1'" in out1
    assert "se_" in out1

    # Extract key id from database
    with test_db.reader() as conn:
        row = conn.execute("SELECT id, key_hash, status FROM api_keys").fetchone()
        assert row is not None
        key_id = str(row[0])
        assert row[2] == "active"

    # 2. List keys
    ret2 = main(["auth", "list-keys", "--json", db_arg])
    assert ret2 == 0
    out2 = capsys.readouterr().out
    keys_list = json.loads(out2)
    assert len(keys_list) == 1
    assert keys_list[0]["id"] == key_id
    assert keys_list[0]["name"] == "worker-1"

    # 3. Revoke key
    ret3 = main(["auth", "revoke-key", key_id, db_arg])
    assert ret3 == 0
    out3 = capsys.readouterr().out
    assert f"Key '{key_id}' has been revoked." in out3

    with test_db.reader() as conn:
        status = conn.execute("SELECT status FROM api_keys WHERE id = ?", (key_id,)).fetchone()[0]
        assert status == "revoked"
