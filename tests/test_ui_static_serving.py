"""Tests for SPAStaticFiles — the production UI static handler.

Guards against the blank-page-after-update failure mode:

1. Content-hashed /assets/* files must be served with an immutable,
   one-year Cache-Control header (safe: the filename changes on rebuild).
2. index.html and sw.js must NEVER be cached (``no-cache``) — a stale
   shell references asset hashes that no longer exist after an update.
3. A missing /assets/* file must return a real 404, never the SPA shell.
   Serving index.html where a .js module was expected makes the browser
   execute HTML as a module script — a silent blank page.
4. 404 responses must never be cached.
5. SPA deep links (client-side routes) must serve the shell with
   ``no-cache`` — including when the build ships a 404.html file, which
   makes StaticFiles(html=True) RETURN a 404 response instead of raising.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orivellum.api.app import SPAStaticFiles

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"

INDEX_HTML = "<!DOCTYPE html><html><body>SPA SHELL</body></html>"
SW_JS = "// service worker"
ASSET_JS = "console.log('app');"
NOTFOUND_HTML = "<!DOCTYPE html><html><body>CUSTOM 404 PAGE</body></html>"


def _build_client(tmp_path, with_404_page: bool) -> TestClient:
    dist = tmp_path / "public"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "sw.js").write_text(SW_JS, encoding="utf-8")
    (dist / "assets" / "index-ABC123.js").write_text(ASSET_JS, encoding="utf-8")
    if with_404_page:
        (dist / "404.html").write_text(NOTFOUND_HTML, encoding="utf-8")

    app = FastAPI()
    app.mount("/orivellum-ui", SPAStaticFiles(directory=str(dist), html=True), name="ui")
    return TestClient(app)


@pytest.fixture()
def client(tmp_path) -> TestClient:
    """Standard build fixture — mirrors the real Vite output (no 404.html)."""
    return _build_client(tmp_path, with_404_page=False)


@pytest.fixture()
def client_with_404_page(tmp_path) -> TestClient:
    """Defensive fixture: a future build that ships a 404.html file."""
    return _build_client(tmp_path, with_404_page=True)


# ── Hashed assets: immutable caching ─────────────────────────────────────────


class TestHashedAssets:
    def test_get_asset_immutable(self, client):
        r = client.get("/orivellum-ui/assets/index-ABC123.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == IMMUTABLE
        assert r.text == ASSET_JS

    def test_head_asset_immutable(self, client):
        r = client.head("/orivellum-ui/assets/index-ABC123.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == IMMUTABLE

    def test_conditional_304_keeps_immutable(self, client):
        first = client.get("/orivellum-ui/assets/index-ABC123.js")
        etag = first.headers["etag"]
        r = client.get(
            "/orivellum-ui/assets/index-ABC123.js",
            headers={"If-None-Match": etag},
        )
        assert r.status_code == 304
        assert r.headers["cache-control"] == IMMUTABLE


# ── Shell + service worker: never cached ─────────────────────────────────────


class TestShellNeverCached:
    @pytest.mark.parametrize("path", ["/orivellum-ui/", "/orivellum-ui/index.html"])
    def test_index_no_cache(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == NO_CACHE
        assert "SPA SHELL" in r.text

    def test_sw_js_no_cache(self, client):
        r = client.get("/orivellum-ui/sw.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == NO_CACHE
        assert r.text == SW_JS


# ── Missing assets: real 404, never the shell, never cached ─────────────────


class TestMissingAssets:
    def test_dead_asset_is_404_not_shell(self, client):
        r = client.get("/orivellum-ui/assets/index-OLDHASH.js")
        assert r.status_code == 404
        assert "SPA SHELL" not in r.text

    def test_dead_asset_never_immutable(self, client):
        r = client.get("/orivellum-ui/assets/index-OLDHASH.js")
        assert r.headers.get("cache-control") != IMMUTABLE

    def test_dead_asset_with_404_page_is_404_not_shell(self, client_with_404_page):
        # html=True + 404.html: StaticFiles RETURNS a 404 response
        # (does not raise). The custom 404 page may be served, but it
        # must keep status 404, never be the SPA shell, never be cached.
        r = client_with_404_page.get("/orivellum-ui/assets/index-OLDHASH.js")
        assert r.status_code == 404
        assert "SPA SHELL" not in r.text
        assert r.headers.get("cache-control") == NO_CACHE


# ── SPA deep links: shell with no-cache ──────────────────────────────────────


class TestSpaRouting:
    @pytest.mark.parametrize(
        "path",
        [
            "/orivellum-ui/works/abc",
            "/orivellum-ui/library/some-doc-id",
            "/orivellum-ui/settings",
        ],
    )
    def test_deep_link_serves_shell(self, client, path):
        r = client.get(path)
        assert r.status_code == 200
        assert "SPA SHELL" in r.text
        assert r.headers["cache-control"] == NO_CACHE

    def test_deep_link_serves_shell_even_with_404_page(self, client_with_404_page):
        # With a 404.html in the build, the unknown route comes back as a
        # RETURNED 404 response — the handler must still swap in the shell
        # so client-side routing keeps working.
        r = client_with_404_page.get("/orivellum-ui/works/abc")
        assert "SPA SHELL" in r.text
        assert "CUSTOM 404 PAGE" not in r.text
        assert r.headers["cache-control"] == NO_CACHE
