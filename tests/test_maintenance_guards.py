"""Regression tests for the confirm-gate on bulk-delete maintenance endpoints.

The endpoints below all accept a request body with a `confirm` boolean. Without
``confirm: true`` they must return 400 with a human-readable detail — never
silently start deleting, and never crash with an unimported-symbol NameError
(which is exactly what shipped briefly when the gates were added).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client() -> TestClient:
    # Import here so conftest's env overrides run first.
    from backend.main import app
    return TestClient(app)


def test_archive_purge_requires_confirm_when_unscoped():
    client = _client()
    r = client.post("/api/maintenance/compress/archive/purge", json={})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def test_archive_purge_allows_scoped_call_without_confirm():
    """A purge scoped by `older_than_days` is a deliberate targeted action —
    the confirm gate only protects the all-null full wipe."""
    client = _client()
    r = client.post(
        "/api/maintenance/compress/archive/purge",
        json={"older_than_days": 30},
    )
    # Either 200 (succeeded with zero matches) or 400 with a non-confirm
    # reason — what we MUST NOT see is the unconfirmed-purge guard tripping.
    assert r.status_code != 400 or "confirm" not in r.json()["detail"].lower()


def test_missing_files_purge_requires_confirm():
    client = _client()
    r = client.post("/api/maintenance/missing-files/purge", json={"confirm": False})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()


def test_short_videos_purge_requires_confirm():
    client = _client()
    r = client.post(
        "/api/maintenance/short-videos/purge?max_seconds=10",
        json={"confirm": False},
    )
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"].lower()
