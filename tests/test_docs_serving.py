"""The app serves the built documentation under /docs (spec docs/architecture/specs/docs-in-app.md, ADR-0043)."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arcviz.server import DOCS_DIR, create_app


@pytest.fixture
def site(tmp_path: Path) -> Path:
    root = tmp_path / "site"
    (root / "guide").mkdir(parents=True)
    (root / "index.html").write_text("<h1>Arcviz docs</h1>", encoding="utf-8")
    (root / "guide" / "index.html").write_text("<h1>User guide</h1>", encoding="utf-8")
    (root / "404.html").write_text("<h1>Not here</h1>", encoding="utf-8")
    (root / "search.json").write_text("{}", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    return root


@pytest.fixture
def client(store, site):
    return TestClient(create_app(store=store, docs_dir=site))


def test_default_docs_dir_is_inside_the_package():
    assert Path(__file__).parent.parent / "arcviz" / "docs_site" == DOCS_DIR


def test_docs_index_and_pages_are_served(client):
    r = client.get("/docs/")
    assert r.status_code == 200 and "Arcviz docs" in r.text
    assert r.headers["content-type"].startswith("text/html")
    assert "User guide" in client.get("/docs/guide/").text


def test_docs_without_trailing_slash_redirects(client):
    for path, target in (("/docs", "/docs/"), ("/docs/guide", "/docs/guide/")):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308), path
        assert r.headers["location"].endswith(target), path


def test_docs_are_revalidated_on_every_use(client):
    for path in ("/docs/", "/docs/search.json"):
        r = client.get(path)
        assert r.headers["cache-control"] == "no-cache", path
        assert "etag" in r.headers or "last-modified" in r.headers, path


def test_unknown_page_is_the_sites_404(client):
    r = client.get("/docs/nope/")
    assert r.status_code == 404 and "Not here" in r.text
    assert r.headers["cache-control"] == "no-cache"  # a page added by an upgrade must not stay a cached 404


@pytest.mark.parametrize(
    "path",
    [
        "/docs/../secret.txt",
        "/docs/%2e%2e/secret.txt",
        "/docs/%2e%2e%2fsecret.txt",
        "/docs/guide/%2e%2e/%2e%2e/secret.txt",
        "/docs/..%5csecret.txt",
        "/docs//etc/passwd",
        "/docs/%2fetc%2fpasswd",
    ],
)
def test_no_path_traversal_out_of_the_site(client, path):
    r = client.get(path)
    assert "SECRET" not in r.text and "root:" not in r.text
    assert r.status_code in (404, 400) or (r.status_code == 200 and "Arcviz docs" in r.text)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlinks_out_of_the_site_are_not_followed(client, site):
    (site / "leak.txt").symlink_to(site.parent / "secret.txt")
    r = client.get("/docs/leak.txt")
    assert r.status_code == 404 and "SECRET" not in r.text


def test_without_a_built_site_docs_answer_404_with_instructions(store, tmp_path):
    client = TestClient(create_app(store=store, docs_dir=tmp_path / "missing"))
    for path in ("/docs", "/docs/", "/docs/guide/", "/docs/%2e%2e/secret.txt"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert "make docs-bundle" in r.text and "docs/ in the repository" in r.text, path


def test_api_and_frontend_are_unaffected_by_the_docs_mount(client):
    assert client.get("/api/config").status_code == 200
    assert client.get("/").status_code == 200


def test_interactive_api_docs_moved_under_api(client):
    assert "swagger" in client.get("/api/docs").text.lower()
    assert "redoc" in client.get("/api/redoc").text.lower()
    assert client.get("/openapi.json").json()["info"]["title"] == "Arcviz agent"
    assert "/docs/{path}" not in client.get("/openapi.json").json()["paths"]


def test_frontend_links_the_user_guide_and_the_architecture_docs():
    html = (Path(__file__).parent.parent / "arcviz" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="/docs/guide/"' in html
    assert 'href="/docs/architecture/arc42/"' in html
