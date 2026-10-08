"""Server-path confinement and URL-ingest fixes for document endpoints."""

import os

import pytest
from fastapi.testclient import TestClient

from app.core.path_guard import PathNotAllowed, resolve_within_roots


@pytest.fixture
def roots(tmp_path, monkeypatch):
    allowed = tmp_path / "data"
    (allowed / "imports").mkdir(parents=True)
    monkeypatch.setenv("SERVER_PATH_ROOTS", str(allowed))
    return allowed


def test_paths_are_confined_to_roots(roots, tmp_path):
    assert resolve_within_roots(roots / "imports") == (roots / "imports").resolve()
    with pytest.raises(PathNotAllowed):
        resolve_within_roots(roots / ".." / "secrets")
    with pytest.raises(PathNotAllowed):
        resolve_within_roots("/etc")
    with pytest.raises(PathNotAllowed):
        resolve_within_roots("")
    # Local desktop mode only resolves.
    assert str(resolve_within_roots("/etc", enforce=False)) == os.path.realpath("/etc")


def test_symlink_escape_is_rejected(roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (roots / "link").symlink_to(outside)
    with pytest.raises(PathNotAllowed):
        resolve_within_roots(roots / "link")


def test_restriction_defaults(monkeypatch):
    from app.core import path_guard

    monkeypatch.delenv("SERVER_PATH_RESTRICT", raising=False)
    monkeypatch.setenv("RUNNING_IN_DOCKER", "1")
    assert path_guard.restriction_enabled() is True
    monkeypatch.setenv("SERVER_PATH_RESTRICT", "false")
    assert path_guard.restriction_enabled() is False


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_directory_import_outside_roots_is_rejected_in_server_mode(client, roots, monkeypatch):
    monkeypatch.setenv("SERVER_PATH_RESTRICT", "true")
    res = client.post("/api/v1/documents/directory", json={"directory_path": "/etc"})
    assert res.status_code == 400 and "outside the allowed" in res.json()["detail"]


def test_url_ingest_uses_ssrf_guarded_scraper(client):
    # Previously every call failed with ImportError (module did not exist) -> 500.
    res = client.post("/api/v1/documents/url", json={"url": "http://127.0.0.1:8000/health"})
    assert res.status_code == 400
    assert "Scraper failed" in res.json()["detail"]


def test_template_merge_rejects_paths_outside_roots(client, roots, monkeypatch):
    from app.core.reports import officecli_service

    class _Svc:
        def is_available(self):
            return True

        async def merge_template(self, *a, **k):  # pragma: no cover - must not be reached
            raise AssertionError("merge called with an unconfined path")

    monkeypatch.setattr(officecli_service, "get_officecli_service", lambda: _Svc())
    res = client.post("/api/v1/documents/merge", json={
        "template_path": str(roots / "imports" / "t.docx"),
        "output_path": "/tmp/evil.docx",
        "data": {},
    })
    assert res.status_code == 400
