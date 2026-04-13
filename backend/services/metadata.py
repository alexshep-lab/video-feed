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


def _pick_primary_video_stream(streams: list[dict]) -> dict:
    """Choose the real video track from an ffprobe streams list.

    MP4/MKV files frequently embed extra "video" streams that aren't the actual
    movie:
      - Cover art / thumbnail (disposition.attached_pic == 1, codec=mjpeg/png)
      - Single-frame placeholder streams (avg_frame_rate "0/0")
      - Auxiliary tracks (e.g. subtitle bitmap streams typed as video)

    The previous implementation just picked the first ``codec_type == "video"``
    entry, which gave us the cover art's dimensions for some files and made
    them appear as oddly-shaped low-res videos in the candidate list.

    Strategy:
      1. Drop attached_pic streams.
      2. Drop streams with no width/height.
      3. Drop image-only codecs (mjpeg, png, gif, bmp, webp).
      4. Pick the remaining stream with the largest pixel area.
      5. Fall back to the first video-typed stream if nothing matched (so we
         still extract *something* for unusual files).
    """
    image_codecs = {"mjpeg", "png", "gif", "bmp", "webp", "tiff"}
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        return {}

    real_videos = []
    for s in video_streams:
        disposition = s.get("disposition") or {}
        if disposition.get("attached_pic"):
            continue
        codec_name = (s.get("codec_name") or "").lower()
        if codec_name in image_codecs:
            continue
        w = _safe_int(s.get("width"))
        h = _safe_int(s.get("height"))
        if not w or not h:
            continue
        real_videos.append((w * h, s))

    if real_videos:
        real_videos.sort(key=lambda item: item[0], reverse=True)
        return real_videos[0][1]
    # Fallback — let the file at least register, even if nothing looked "real"
    return video_streams[0]


def extract_video_metadata(path: Path) -> dict:
    payload = probe_video(path)
    streams = payload.get("streams", [])
    video_stream = _pick_primary_video_stream(streams)
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

