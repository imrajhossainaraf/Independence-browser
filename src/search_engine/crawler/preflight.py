"""Preflight crawl authorization and integrity hashing (decision D13).

Real mode refuses to start unless data/preflight.approved.json exists and matches
a hash of seeds, news sources, entity list, allowlist, user-agent, image policy, and limits.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from search_engine.config import Settings
from search_engine.errors import PolicyDenied


def _read_file_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@dataclass(frozen=True)
class PreflightManifest:
    """Canonical manifest of all inputs and policies required for crawl authorization."""

    seeds: list[str]
    news_sources: list[str]
    entities: list[str]
    allowlist_mode: bool
    user_agent_token: str
    policy_url: str
    news_images_enabled: bool
    max_pages: int
    max_depth: int
    max_pages_per_host: int
    max_storage_bytes: int


def build_manifest(settings: Settings) -> PreflightManifest:
    """Build canonical manifest from effective settings and data directory files."""
    seeds = sorted(_read_file_lines(settings.seeds_file))
    news_sources = sorted(_read_file_lines(settings.news_sources_file))
    entities = sorted(_read_file_lines(settings.entities_seed_file))

    return PreflightManifest(
        seeds=seeds,
        news_sources=news_sources,
        entities=entities,
        allowlist_mode=settings.allowlist_mode,
        user_agent_token=settings.user_agent_token,
        policy_url=settings.policy_url,
        news_images_enabled=settings.news_images_enabled,
        max_pages=settings.max_pages,
        max_depth=settings.max_depth,
        max_pages_per_host=settings.max_pages_per_host,
        max_storage_bytes=settings.max_storage_bytes,
    )


def compute_manifest_hash(manifest: PreflightManifest) -> str:
    """Compute deterministic SHA-256 hash over canonical JSON representation."""
    canonical_json = json.dumps(asdict(manifest), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def create_preflight_approval(settings: Settings, target_path: Path | None = None) -> Path:
    """Generate and write data/preflight.approved.json for the current configuration."""
    manifest = build_manifest(settings)
    digest = compute_manifest_hash(manifest)
    out_path = target_path or (settings.seeds_file.parent / "preflight.approved.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "hash": digest,
        "created_at": datetime.now(UTC).isoformat(),
        "manifest": asdict(manifest),
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def verify_preflight_approval(settings: Settings, preflight_path: Path | None = None) -> None:
    """Verify that real network crawling is authorized and preflight matches.

    Raises PolicyDenied on any discrepancy or missing file.
    """
    if not settings.real_network_enabled:
        raise PolicyDenied(
            "real network is disabled; set real_network_enabled=true "
            "and provide preflight approval",
            code="real_network_disabled",
        )

    path = preflight_path or (settings.seeds_file.parent / "preflight.approved.json")
    if not path.exists():
        raise PolicyDenied(
            f"preflight approval file not found: {path}. "
            "Run 'search-engine approve-preflight' first.",
            code="preflight_missing",
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        approved_hash = str(data["hash"])
    except (json.JSONDecodeError, KeyError, OSError) as exc:
        raise PolicyDenied(
            f"invalid preflight approval file: {path}",
            code="preflight_invalid",
        ) from exc

    current_manifest = build_manifest(settings)
    current_hash = compute_manifest_hash(current_manifest)

    if not secrets.compare_digest(approved_hash, current_hash):
        raise PolicyDenied(
            "preflight approval file does not match current inputs/settings. "
            "Re-run 'search-engine approve-preflight' to re-approve.",
            code="preflight_mismatch",
        )
