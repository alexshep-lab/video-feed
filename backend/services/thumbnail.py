from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from ..config import get_settings
from .encoder import build_hw_decode_args

logger = logging.getLogger("videofeed.thumbnail")

# Tracks running ffmpeg procs across all thumbnail/palette threads so that
# ``kill_running_ffmpeg_procs()`` can interrupt them — otherwise server
# shutdown and the "Stop" palette button block on whatever ffmpeg is crunching
# a slow file in a worker thread.
_running_procs: set[subprocess.Popen] = set()


def kill_running_ffmpeg_procs() -> int:
    """Kill every thumbnail/palette ffmpeg currently running. Returns kill count."""
    killed = 0
    for proc in list(_running_procs):
        try:
            proc.kill()
            killed += 1
        except Exception:
            pass
    return killed


def _run_one(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    _running_procs.add(proc)
    try:
        out, err = proc.communicate()
    finally:
        _running_procs.discard(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _run_ffmpeg_with_hw_fallback(
    cmd_builder, label: str
) -> subprocess.CompletedProcess:
    """Run ffmpeg first with HW decode args, fall back to pure CPU on failure.

    `cmd_builder` is a callable that takes a list of pre-input args (e.g.
    ``["-hwaccel", "cuda"]`` or ``[]``) and returns the full ffmpeg argv.
    """
    hw_args = build_hw_decode_args()
    if hw_args:
        result = _run_one(cmd_builder(hw_args))
        if result.returncode == 0:
            return result
        logger.warning(
            "%s: HW decode failed (returncode=%s), retrying on CPU. stderr=%s",
            label, result.returncode, (result.stderr or "").strip()[:200],
        )
    return _run_one(cmd_builder([]))


def invalidate_video_cache(video_id: str) -> None:
    """Drop every cached derived asset for a video so the next request regenerates.

    Used when the source file content changes (compression, conversion, manual
    re-encode) — the cached thumbnail / contact sheet / preview frames were
    generated from the old content and no longer match.
    """
    settings = get_settings()
    targets: list[Path] = [
        settings.thumbnails_dir / f"{video_id}.jpg",
        settings.media_dir / "contact_sheets" / f"{video_id}.jpg",
    ]
    for target in targets:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

    frame_dir = settings.preview_frames_dir / video_id
    if frame_dir.exists() and frame_dir.is_dir():
        for child in frame_dir.iterdir():
            try:
                child.unlink()
            except OSError:
                pass


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

    def build(hw_args: list[str]) -> list[str]:
        # -ss before -i is "fast seek" — much cheaper than decoding from start.
        # When HW decode is enabled it must come before -ss/-i too.
        return [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel", "error",
            *hw_args,
            "-ss", f"{timestamp:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "2",
            "-y",
            str(target),
        ]

    completed = _run_ffmpeg_with_hw_fallback(build, label=f"ensure_frame {target.name}")
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
    """Generate a single image with a grid of frames (16 frames at 4x4 default).

    Decode is offloaded to NVDEC when CUDA hwaccel is available — this is the
    big win for contact sheets, because the ``select`` filter forces ffmpeg
    to walk the *entire* video to count frames. CPU fallback is automatic.
    """
    settings = get_settings()
    target = settings.media_dir / "contact_sheets" / f"{video_id}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and target.stat().st_size > 0:
        return target

    if not duration or duration <= 0:
        duration = 1.0

    n = cols * rows
    # Pick every Nth frame so we end up with ~n frames spread across the file.
    # Assumes ~25fps as a rough constant; exact accuracy isn't critical for a thumbnail grid.
    every_nth = max(1, int(duration * 25 / n))
    vf = f"select='not(mod(n\\,{every_nth}))',scale={tile_width}:-2,tile={cols}x{rows}"

    def build(hw_args: list[str]) -> list[str]:
        return [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel", "error",
            *hw_args,
            "-i", str(video_path),
            "-vf", vf,
            "-frames:v", "1",
            "-vsync", "vfr",
            "-q:v", "3",
            "-y",
            str(target),
        ]

    completed = _run_ffmpeg_with_hw_fallback(build, label=f"contact_sheet {video_id}")
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
