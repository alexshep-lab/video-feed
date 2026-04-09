from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from ..config import get_settings


def generate_thumbnail(video_path: Path, video_id: str, duration: float | None) -> Path:
    settings = get_settings()
    target = settings.thumbnails_dir / f"{video_id}.jpg"
    timestamp = choose_thumbnail_timestamp(duration)
    return ensure_frame(video_path, target, timestamp)


def generate_preview_frame(
    video_path: Path,
    video_id: str,
    frame_index: int,
    duration: float | None,
    total_frames: int = 8,
) -> Path:
    settings = get_settings()
    safe_index = max(0, min(frame_index, total_frames - 1))
    frame_dir = settings.preview_frames_dir / video_id
    target = frame_dir / f"{safe_index}.jpg"
    timestamp = choose_preview_timestamp(duration, safe_index, total_frames)
    return ensure_frame(video_path, target, timestamp)


def ensure_frame(video_path: Path, target: Path, timestamp: float) -> Path:
    settings = get_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target

    command = [
        settings.ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        str(target),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0 or not target.exists():
        raise RuntimeError(completed.stderr.strip() or "ffmpeg failed to generate preview")
    return target


def choose_thumbnail_timestamp(duration: float | None) -> float:
    if not duration or duration <= 0:
        return 1.0
    return max(0.0, min(duration * 0.25, max(duration - 0.2, 0.0)))


def choose_preview_timestamp(duration: float | None, frame_index: int, total_frames: int) -> float:
    if not duration or duration <= 0:
        return float(frame_index + 1)

    usable_start = min(duration * 0.08, max(duration - 0.2, 0.0))
    usable_end = min(duration * 0.88, max(duration - 0.2, 0.0))
    if usable_end <= usable_start:
        return choose_thumbnail_timestamp(duration)

    if total_frames <= 1:
        return usable_start

    step = (usable_end - usable_start) / (total_frames - 1)
    return usable_start + frame_index * step


def generate_contact_sheet(
    video_path: Path,
    video_id: str,
    duration: float | None,
    cols: int = 4,
    rows: int = 4,
    tile_width: int = 480,
) -> Path:
    """Generate a single image with a grid of frames (16 frames at 4x4 default)."""
    settings = get_settings()
    target = settings.media_dir / "contact_sheets" / f"{video_id}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and target.stat().st_size > 0:
        return target

    if not duration or duration <= 0:
        duration = 1.0

    n = cols * rows
    # Use ffmpeg select filter to extract evenly distributed frames + tile filter
    # interval ensures we skip the first and last 5%
    start = duration * 0.05
    end = duration * 0.95
    if end <= start:
        end = duration
    interval = (end - start) / n

    # Build a complex filter: extract n frames at even intervals, scale, tile
    select_expr = "+".join([f"eq(n\\,{int((start + i * interval) * 25)})" for i in range(n)])
    # Simpler approach: use fps filter
    cmd = [
        settings.ffmpeg_binary,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"select='not(mod(n\\,{max(1, int(duration * 25 / n))}))',scale={tile_width}:-2,tile={cols}x{rows}",
        "-frames:v", "1",
        "-vsync", "vfr",
        "-q:v", "3",
        "-y",
        str(target),
    ]
    completed = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=False,
    )
    if completed.returncode != 0 or not target.exists():
        raise RuntimeError(completed.stderr.strip() or "ffmpeg contact sheet failed")
    return target


def fallback_svg_bytes(label: str) -> bytes:
    digest = hashlib.md5(label.encode("utf-8", errors="ignore")).hexdigest()
    left = f"#{digest[:6]}"
    right = f"#{digest[6:12]}"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">
<defs>
<linearGradient id="g" x1="0" x2="1" y1="0" y2="1">
<stop offset="0%" stop-color="{left}"/>
<stop offset="100%" stop-color="{right}"/>
</linearGradient>
</defs>
<rect width="1280" height="720" fill="url(#g)"/>
<rect width="1280" height="720" fill="rgba(8,12,20,0.45)"/>
<text x="640" y="360" text-anchor="middle" fill="rgba(255,255,255,0.72)"
      font-family="Segoe UI, sans-serif" font-size="52" letter-spacing="10">VIDEO</text>
</svg>"""
    return svg.encode("utf-8")
