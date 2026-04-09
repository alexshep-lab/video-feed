"""HLS transcoding service using FFmpeg.

Runs as a background asyncio task with a queue. Generates multi-quality
HLS variants based on source resolution.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import Video

logger = logging.getLogger("videofeed.transcoder")

# Quality presets: (name, height, video_bitrate, audio_bitrate)
QUALITY_PRESETS = [
    ("1080p", 1080, "5000k", "192k"),
    ("720p", 720, "2500k", "128k"),
    ("480p", 480, "1000k", "96k"),
]

# Global queue and state
_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_current_video_id: str | None = None


def get_queue_status() -> dict:
    return {
        "queue_size": _queue.qsize(),
        "current_video_id": _current_video_id,
        "worker_running": _worker_task is not None and not _worker_task.done(),
    }


def enqueue_video(video_id: str) -> None:
    _queue.put_nowait(video_id)


def enqueue_all_pending(session: Session) -> int:
    """Enqueue all videos with transcode_status='pending'."""
    videos = session.scalars(
        select(Video).where(Video.transcode_status == "pending")
    ).all()
    count = 0
    for video in videos:
        _queue.put_nowait(video.id)
        count += 1
    return count


def start_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        # Send poison pill so the worker wakes up and exits
        _queue.put_nowait("__STOP__")
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _worker_task = None


async def _worker_loop() -> None:
    global _current_video_id
    logger.info("Transcoder worker started")
    while True:
        video_id = await _queue.get()
        if video_id == "__STOP__":
            break
        _current_video_id = video_id
        try:
            await _transcode_video(video_id)
        except Exception:
            logger.exception("Transcode failed for %s", video_id)
            _update_status(video_id, "failed", 0.0)
        finally:
            _current_video_id = None
            _queue.task_done()


async def _transcode_video(video_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if not video:
            logger.warning("Video %s not found, skipping", video_id)
            return

        src_path = Path(video.original_path)
        if not src_path.exists():
            logger.warning("Source file missing: %s", src_path)
            _update_status(video_id, "failed", 0.0)
            return

        src_height = video.height or 0
        src_width = video.width or 0
        duration = video.duration or 0

        # Determine which quality levels to produce
        qualities = _select_qualities(src_height)
        if not qualities:
            qualities = [("original", max(src_height, 240), "1500k", "128k")]

        hls_dir = settings.hls_dir / video_id
        if hls_dir.exists():
            shutil.rmtree(hls_dir)
        hls_dir.mkdir(parents=True, exist_ok=True)

    _update_status(video_id, "processing", 0.0)

    master_entries = []
    total_steps = len(qualities)

    for step, (name, height, vbr, abr) in enumerate(qualities):
        out_dir = hls_dir / name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Scale filter: preserve aspect ratio, ensure even dimensions
        scale = f"scale=-2:{height}" if src_height > 0 else f"scale=-2:{height}"

        cmd = [
            settings.ffmpeg_binary,
            "-i", str(src_path),
            "-vf", scale,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-b:v", vbr,
            "-maxrate", vbr,
            "-bufsize", str(int(vbr.replace("k", "")) * 2) + "k",
            "-c:a", "aac",
            "-b:a", abr,
            "-ac", "2",
            "-hls_time", "4",
            "-hls_playlist_type", "vod",
            "-hls_segment_filename", str(out_dir / "segment%03d.ts"),
            "-y",
            str(out_dir / "stream.m3u8"),
        ]

        logger.info("Transcoding %s @ %s: %s", video_id, name, " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Parse progress from stderr
        progress_base = step / total_steps
        progress_step = 1.0 / total_steps

        async def read_progress():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", text)
                if match and duration > 0:
                    h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
                    current = h * 3600 + m * 60 + s
                    step_progress = min(current / duration, 1.0)
                    total_progress = progress_base + step_progress * progress_step
                    _update_status(video_id, "processing", round(total_progress * 100, 1))

        await read_progress()
        await proc.wait()

        if proc.returncode != 0:
            stderr_out = await proc.stderr.read()
            logger.error("FFmpeg failed for %s @ %s: %s", video_id, name, stderr_out.decode(errors="replace"))
            _update_status(video_id, "failed", 0.0)
            return

        # Determine bandwidth and resolution for master playlist
        bandwidth = int(vbr.replace("k", "")) * 1000
        if src_height > 0 and src_width > 0:
            aspect = src_width / src_height
            res_width = round(aspect * height / 2) * 2
        else:
            res_width = round(height * 16 / 9 / 2) * 2

        master_entries.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res_width}x{height}\n{name}/stream.m3u8"
        )

    # Write master playlist
    master_content = "#EXTM3U\n" + "\n".join(master_entries) + "\n"
    master_path = hls_dir / "master.m3u8"
    master_path.write_text(master_content, encoding="utf-8")

    # Update DB
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if video:
            video.transcode_status = "completed"
            video.transcode_progress = 100.0
            video.hls_path = str(master_path)
            session.commit()

    logger.info("Transcode complete for %s", video_id)


def _select_qualities(src_height: int) -> list[tuple[str, int, str, str]]:
    """Select quality presets that don't upscale the source."""
    return [(name, h, vbr, abr) for name, h, vbr, abr in QUALITY_PRESETS if h <= src_height]


def _update_status(video_id: str, status: str, progress: float) -> None:
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if video:
            video.transcode_status = status
            video.transcode_progress = progress
            session.commit()
