from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import get_settings


SUPPORTED_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".wmv"}


def is_supported_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def probe_video(path: Path) -> dict:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffprobe failed")
    return json.loads(completed.stdout or "{}")


def extract_video_metadata(path: Path) -> dict:
    payload = probe_video(path)
    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    format_data = payload.get("format", {})

    width = _safe_int(video_stream.get("width"))
    height = _safe_int(video_stream.get("height"))

    return {
        "duration": _safe_float(format_data.get("duration")),
        "width": width,
        "height": height,
        "codec": video_stream.get("codec_name"),
        "is_vertical": bool(width and height and height > width),
    }


def ffprobe_available() -> bool:
    try:
        probe_video(Path(__file__))
    except FileNotFoundError:
        return False
    except RuntimeError:
        return True
    return True


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

