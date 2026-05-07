"""Subprocess-spawn helpers shared across services.

The ffmpeg/ffprobe wrappers run dozens of short-lived processes per scan
or transcode. In a PyInstaller --noconsole Windows bundle every one of
them would flash a black cmd window without CREATE_NO_WINDOW. The kwargs
below get spread into every subprocess.run / Popen / create_subprocess_exec
call so that flicker is suppressed in the bundle (and is a no-op when run
from source or on non-Windows).
"""
from __future__ import annotations

import subprocess
import sys


if sys.platform == "win32":
    HIDDEN_SUBPROCESS_KWARGS: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    HIDDEN_SUBPROCESS_KWARGS = {}
