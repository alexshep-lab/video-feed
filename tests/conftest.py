"""Shared fixtures.

The backend reads its database/media directories from settings at import
time. Tests need each module to start from a clean slate, so we point
``LOCALAPPDATA`` and the library dirs at a tmp directory *before* any
backend module is imported.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest


# Point the app at a throwaway data dir so the test run doesn't touch the
# user's real %LOCALAPPDATA%\VideoFeed.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="videofeed-tests-"))
os.environ["LOCALAPPDATA"] = str(_TMP_ROOT / "appdata")
os.environ["VIDEOFEED_LIBRARY_DIRS_RAW"] = str(_TMP_ROOT / "library")
os.environ.pop("VIDEOFEED_CONVERTED_DIR_RAW", None)
os.environ.pop("VIDEOFEED_CORS_ORIGINS_RAW", None)

# Make the project root importable so `from backend ...` works.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return _TMP_ROOT
