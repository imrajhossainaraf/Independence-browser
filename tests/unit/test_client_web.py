"""Unit and static analysis tests for the thin web client (Step 23, Milestone M2)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from search_engine.api.app import create_app
from search_engine.config import load_settings
from search_engine.db import Database

pytestmark = pytest.mark.loopback
STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "search_engine" / "api" / "static"


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    db = Database(tmp_path / "test_client.db").open()
    yield db
    db.close()


def test_root_and_static_assets_served_correctly(test_db: Database) -> None:
    """GET / and /static/* serve valid static web client files."""
    settings = load_settings(
        None, {"db_path": str(test_db.path), "allow_loopback_for_tests": "true"}
    )
    app = create_app(test_db, settings=settings)
    client = TestClient(app)

    # 1. Root serves index.html
    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert "text/html" in resp_root.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in resp_root.text
    assert "Independent Search" in resp_root.text

    # 2. News archive route serves index.html for client routing
    resp_news = client.get("/news")
    assert resp_news.status_code == 200
    assert "<!DOCTYPE html>" in resp_news.text

    # 3. CSS stylesheet
    resp_css = client.get("/static/style.css")
    assert resp_css.status_code == 200
    assert "text/css" in resp_css.headers.get("content-type", "")
    assert "--bg-primary" in resp_css.text

    # 4. JavaScript client
    resp_js = client.get("/static/app.js")
    assert resp_js.status_code == 200
    assert "javascript" in resp_js.headers.get("content-type", "")
    assert "Independent Search Engine - Thin Web Client" in resp_js.text


def test_client_javascript_size_budget() -> None:
    """Milestone M2: Total uncompressed client JavaScript must be under 50 KB."""
    app_js = STATIC_DIR / "app.js"
    assert app_js.is_file(), "app.js must exist"
    size_bytes = app_js.stat().st_size
    assert size_bytes < 50 * 1024, f"app.js ({size_bytes} bytes) exceeds 50 KB ceiling"


def test_strict_xss_safety_static_audit() -> None:
    """Invariant 12: Client script MUST NEVER use innerHTML or inject unescaped content."""
    app_js = STATIC_DIR / "app.js"
    content = app_js.read_text(encoding="utf-8")

    # Assert no innerHTML or outerHTML assignments
    assert ".innerHTML" not in content, "Forbidden innerHTML found in app.js"
    assert ".outerHTML" not in content, "Forbidden outerHTML found in app.js"
    assert "document.write" not in content, "Forbidden document.write found in app.js"

    # Assert no third-party CDN or remote scripts loaded
    assert "http://" not in content, "Forbidden hard-coded http URL in app.js"
    assert "https://" not in content, "Forbidden hard-coded https URL in app.js"


def test_accessible_markup_wcag_structure() -> None:
    """WCAG 2.2 AA accessibility audit on index.html: landmarks, labels, and WAI-ARIA combobox."""
    index_html = STATIC_DIR / "index.html"
    assert index_html.is_file(), "index.html must exist"
    html = index_html.read_text(encoding="utf-8")

    # 1. HTML language attribute
    assert '<html lang="en">' in html

    # 2. Semantic landmarks
    assert 'role="banner"' in html or "<header" in html
    assert 'role="main"' in html or "<main" in html
    assert 'role="contentinfo"' in html or "<footer" in html
    assert 'role="search"' in html

    # 3. Form input has associated label
    assert '<label for="search-input"' in html
    assert 'id="search-input"' in html

    # 4. WAI-ARIA Combobox pattern
    assert 'role="combobox"' in html
    assert 'aria-autocomplete="list"' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="suggestions-list"' in html
    assert 'id="suggestions-list"' in html
    assert 'role="listbox"' in html

    # 5. Alert roles on notification banners
    assert 'id="stale-banner"' in html
    assert 'role="alert"' in html
    assert 'id="error-banner"' in html

    # 6. Responsive viewport meta tag
    assert '<meta name="viewport"' in html


def test_dual_view_dom_components_present() -> None:
    """Index HTML must contain both Home and Results components with clean initial visibility."""
    index_html = STATIC_DIR / "index.html"
    html = index_html.read_text(encoding="utf-8")

    # Home view components
    assert 'id="home-view"' in html
    assert 'id="top-stories-grid"' in html
    assert 'id="category-pills"' in html
    assert 'id="latest-news-list"' in html
    assert 'id="sources-health-list"' in html

    # Results view components (initially hidden)
    assert 'id="results-view"' in html
    assert 'id="synopsis-box"' in html
    assert 'id="search-results-list"' in html
    assert 'id="search-recent-news"' in html
    assert 'id="sidebar-container"' in html
    assert 'id="preview-card"' in html
    assert 'id="pagination-nav"' in html
    assert 'id="did-you-mean-container"' in html

    # Excerpt grounding label
    assert "Excerpt (100% Grounded in Document)" in html


def test_css_reduced_motion_and_contrast() -> None:
    """CSS must respect prefers-reduced-motion and dark color-scheme."""
    style_css = STATIC_DIR / "style.css"
    assert style_css.is_file(), "style.css must exist"
    css = style_css.read_text(encoding="utf-8")

    # Dark mode support
    assert "@media (prefers-color-scheme: dark)" in css

    # Reduced motion respect
    assert "@media (prefers-reduced-motion: reduce)" in css

    # Visible focus ring
    assert ":focus-visible" in css

    # Responsive breakpoint
    assert "@media (min-width: 768px)" in css
