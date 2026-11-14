"""Read-only HTTP API and thin client (never imports net or crawler)."""

from __future__ import annotations

from search_engine.api.app import create_app, export_openapi_schema

__all__ = ["create_app", "export_openapi_schema"]
