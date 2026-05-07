from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

from ..config import get_settings
from .encoder import build_hw_decode_args
from .proc_utils import HIDDEN_SUBPROCESS_KWARGS


def _ffmpeg_input_path(path: Path) -> str:
    """Return a path string safe to pass as ffmpeg's -i argument.

    FFmpeg on Windows reads argv through the ANSI codepage, so paths with
    emoji / private-use unicode (common in downloaded filenames) arrive
    mangled and fail with "No such file or directory". Convert to the 8.3
    short path when available — that form is pure ASCII.
    """
    resolved = str(path)
    if os.name != "nt":
        return resolved
    try:
        import ctypes
        from ctypes import wintypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetShortPathNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(512)
        length = GetShortPathNameW(resolved, buf, 512)
        if 0 < length < 512 and buf.value:
            return buf.value
    except Exception:
        pass
    return resolved

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
        **HIDDEN_SUBPROCESS_KWARGS,
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

    input_path = _ffmpeg_input_path(video_path)

    def build(hw_args: list[str]) -> list[str]:
        # -ss before -i is "fast seek" — much cheaper than decoding from start.
        # When HW decode is enabled it must come before -ss/-i too.
        return [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel", "error",
            *hw_args,
            "-ss", f"{timestamp:.3f}",
            "-i", input_path,
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
    tile_height: int = 270,
) -> Path:
    """Generate a grid of frames (16 frames at 4x4 default) via multi-seek.

    Uses one ffmpeg invocation with N fast-seek inputs (``-ss T -i video`` per
    tile) and a filter_complex that scales + tiles them. Each ``-ss`` before
    ``-i`` jumps to the nearest keyframe, so total decode is ~N × few frames
    instead of the entire video — orders of magnitude faster than the previous
    ``select`` filter on long files. NVDEC is still used when available, with
    CPU fallback for WMV/VC-1 on cards that don't support them.
    """
    settings = get_settings()
    target = settings.media_dir / "contact_sheets" / f"{video_id}.jpg"
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and target.stat().st_size > 0:
        return target

    if not duration or duration <= 0:
        duration = 1.0

    n = cols * rows
    # Spread timestamps across the video, skipping the very start (intros) and
    # tail (credits / freeze frames) so the grid looks like actual content.
    start = duration * 0.05
    end = max(start + 0.1, duration * 0.95)
    step = (end - start) / (n - 1) if n > 1 else 0.0
    timestamps = [start + i * step for i in range(n)]

    def build(hw_args: list[str]) -> list[str]:
        cmd: list[str] = [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel", "error",
        ]
        input_path = _ffmpeg_input_path(video_path)
        for ts in timestamps:
            cmd.extend([*hw_args, "-ss", f"{ts:.3f}", "-i", input_path])
        # Scale each input to identical dims (required by tile), then stack.
        scale_chains = ";".join(
            f"[{i}:v]trim=end_frame=1,scale={tile_width}:{tile_height}:force_original_aspect_ratio=decrease,"
            f"pad={tile_width}:{tile_height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v{i}]"
            for i in range(n)
        )
        stack_inputs = "".join(f"[v{i}]" for i in range(n))
        layout = "|".join(
            f"{(i % cols) * tile_width}_{(i // cols) * tile_height}" for i in range(n)
        )
        filter_complex = (
            f"{scale_chains};{stack_inputs}xstack=inputs={n}:layout={layout}"
        )
        cmd.extend([
            "-filter_complex", filter_complex,
            "-frames:v", "1",
            "-q:v", "3",
            "-y",
            str(target),
        ])
        return cmd

    completed = _run_ffmpeg_with_hw_fallback(build, label=f"contact_sheet {video_id}")
    if completed.returncode == 0 and target.exists():
        return target

    # Multi-seek xstack failed (corrupted h264 NAL stream, mpeg4 oddities, etc).
    # Fall back to a single mid-video frame upscaled to the sheet size — better
    # a one-tile palette than nothing, and the user can still tell what the clip is.
    logger.warning(
        "contact_sheet %s: multi-seek failed (rc=%s), trying single-frame fallback",
        video_id, completed.returncode,
    )
    sheet_w = tile_width * cols
    sheet_h = tile_height * rows
    midpoint = duration / 2.0
    input_path = _ffmpeg_input_path(video_path)

    def build_single(hw_args: list[str]) -> list[str]:
        return [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel", "error",
            *hw_args,
            "-ss", f"{midpoint:.3f}",
            "-i", input_path,
            "-frames:v", "1",
            "-vf",
            f"scale={sheet_w}:{sheet_h}:force_original_aspect_ratio=decrease,"
            f"pad={sheet_w}:{sheet_h}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-q:v", "3",
            "-y",
            str(target),
        ]

    fallback = _run_ffmpeg_with_hw_fallback(build_single, label=f"contact_sheet {video_id} (fallback)")
    if fallback.returncode != 0 or not target.exists():
        raise RuntimeError(
            (fallback.stderr or completed.stderr or "ffmpeg contact sheet failed").strip()
        )
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
