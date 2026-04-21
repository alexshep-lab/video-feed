"""Config parsing smoke tests."""
from __future__ import annotations

import os


def test_library_dirs_parsed_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEOFEED_LIBRARY_DIRS_RAW", f"{tmp_path};{tmp_path / 'b'}")
    from backend.config import reload_settings
    s = reload_settings()
    paths = s.library_dirs
    assert len(paths) == 2


def test_cors_origins_default_is_loopback_only():
    from backend.config import reload_settings
    # Ensure cors env is unset
    os.environ.pop("VIDEOFEED_CORS_ORIGINS_RAW", None)
    s = reload_settings()
    assert all("localhost" in o or "127.0.0.1" in o for o in s.cors_origins)


def test_cors_origins_can_be_overridden(monkeypatch):
    monkeypatch.setenv("VIDEOFEED_CORS_ORIGINS_RAW", "http://x.example;http://y.example")
    from backend.config import reload_settings
    s = reload_settings()
    assert s.cors_origins == ["http://x.example", "http://y.example"]
