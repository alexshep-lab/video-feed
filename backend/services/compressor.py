"""Video compression service.

Compresses oversized videos to FHD (1920x1080) max.
Output goes to {source_folder}/squized/{filename}.
Background queue runs one job at a time.
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
from .metadata import extract_video_metadata
from .thumbnail import generate_thumbnail

logger = logging.getLogger("videofeed.compressor")

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_current_video_id: str | None = None
_batch_total_jobs = 0
_batch_completed_jobs = 0
_batch_failed_jobs = 0

# Target settings
TARGET_HEIGHT = 1080
CRF = 22
PRESET = "slow"
AUDIO_BITRATE = "128k"


def get_compress_status() -> dict:
    current_title = None
    current_progress = 0.0
    if _current_video_id:
        with SessionLocal() as session:
            video = session.get(Video, _current_video_id)
            if video:
                current_title = video.original_filename
                current_progress = video.compress_progress or 0.0

    overall_progress = 0.0
    if _batch_total_jobs > 0:
        overall_progress = min(
            (
                _batch_completed_jobs
                + (_batch_failed_jobs * 0.0)
                + (current_progress / 100 if _current_video_id else 0.0)
            )
            / _batch_total_jobs
            * 100,
            100.0,
        )

    return {
        "queue_size": _queue.qsize(),
        "current_video_id": _current_video_id,
        "current_video_title": current_title,
        "current_progress": round(current_progress, 1),
        "worker_running": _worker_task is not None and not _worker_task.done(),
        "batch_total_jobs": _batch_total_jobs,
        "batch_completed_jobs": _batch_completed_jobs,
        "batch_failed_jobs": _batch_failed_jobs,
        "overall_progress": round(overall_progress, 1),
    }


def enqueue_compress(video_id: str) -> None:
    _begin_tracking(1)
    _queue.put_nowait(video_id)


def get_oversized_candidates(session: Session, min_height: int = 1440, force: bool = False) -> list[Video]:
    statement = select(Video).where(
        Video.height.is_not(None),
        Video.height > min_height,
        Video.deleted_at.is_(None),
    )
    if force:
        statement = statement.where(Video.compress_status.not_in(["processing", "pending"]))
    else:
        statement = statement.where(Video.compress_status.in_(["none", "failed"]))
    return session.scalars(statement).all()


def count_oversized_candidates(session: Session, min_height: int = 1440, force: bool = False) -> int:
    return len(get_oversized_candidates(session, min_height=min_height, force=force))


def enqueue_oversized(session: Session, min_height: int = 1440, force: bool = False) -> int:
    """Queue all oversized videos that are eligible for compression."""
    videos = get_oversized_candidates(session, min_height=min_height, force=force)
    queued_ids = {item for item in list(_queue._queue) if item != "__STOP__"}  # type: ignore[attr-defined]
    if _current_video_id:
        queued_ids.add(_current_video_id)

    count = 0
    for v in videos:
        if v.id in queued_ids:
            continue
        _queue.put_nowait(v.id)
        v.compress_status = "pending"
        v.compress_progress = 0.0
        count += 1
    if count > 0:
        _begin_tracking(count)
    session.commit()
    return count


def build_archive_path(src: Path, archive_root: Path) -> Path:
    try:
        relative = src.relative_to(archive_root.parent)
        target = archive_root / relative
    except ValueError:
        target = archive_root / src.name

    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return target

    counter = 1
    while True:
        candidate = target.with_name(f"{target.stem}_{counter}{target.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def build_compressed_output_path(src: Path | str) -> Path:
    src = Path(src)
    return src.with_name(f"{src.stem} FHD.mp4")


def start_compress_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_compress_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _queue.put_nowait("__STOP__")
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


async def _worker_loop() -> None:
    global _current_video_id, _batch_completed_jobs, _batch_failed_jobs
    logger.info("Compress worker started")
    while True:
        video_id = await _queue.get()
        if video_id == "__STOP__":
            break
        _current_video_id = video_id
        try:
            await _compress_video(video_id)
            _batch_completed_jobs += 1
        except Exception:
            logger.exception("Compress failed for %s", video_id)
            _update_status(video_id, "failed", 0.0)
            _batch_failed_jobs += 1
        finally:
            _current_video_id = None
            _queue.task_done()


async def _compress_video(video_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if not video:
            return
        src = Path(video.original_path)
        if not src.exists():
            _update_status(video_id, "failed", 0.0)
            return
        duration = video.duration or 0

    out_path = build_compressed_output_path(src)
    archive_path = build_archive_path(src, settings.big_archive_dir)

    _update_status(video_id, "processing", 0.0)

    cmd = [
        settings.ffmpeg_binary,
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-progress", "pipe:2",
        "-i", str(src),
        "-vf", f"scale='min(1920,iw)':'min({TARGET_HEIGHT},ih)':force_original_aspect_ratio=decrease",
        "-c:v", "libx264",
        "-preset", PRESET,
        "-crf", str(CRF),
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        "-y",
        str(out_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace")
        match = re.search(r"out_time=(\d+):(\d+):(\d+\.\d+)", text)
        if match and duration > 0:
            h, m, s = int(match.group(1)), int(match.group(2)), float(match.group(3))
            current = h * 3600 + m * 60 + s
            pct = min(current / duration * 100, 99.0)
            _update_status(video_id, "processing", round(pct, 1))

    await proc.wait()

    if proc.returncode != 0 or not out_path.exists():
        _update_status(video_id, "failed", 0.0)
        return

    new_size = out_path.stat().st_size
    shutil.move(str(src), str(archive_path))

    try:
        new_metadata = extract_video_metadata(out_path)
    except Exception:
        new_metadata = {}

    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if video:
            video.original_path = str(out_path)
            video.original_filename = out_path.name
            video.library_path = str(out_path.parent)
            video.file_size = new_size
            video.file_mtime = out_path.stat().st_mtime
            video.duration = new_metadata.get("duration", video.duration)
            video.width = new_metadata.get("width", video.width)
            video.height = new_metadata.get("height", video.height)
            video.codec = new_metadata.get("codec", video.codec)
            video.is_vertical = new_metadata.get("is_vertical", video.is_vertical)
            video.compress_status = "completed"
            video.compress_progress = 100.0
            video.compressed_path = str(out_path)
            video.compressed_size = new_size
            try:
                thumbnail = generate_thumbnail(out_path, video.id, video.duration)
                video.thumbnail_path = str(thumbnail)
            except Exception:
                pass
            session.commit()
    logger.info(
        "Compressed %s -> %s (%d bytes), archived original to %s",
        video_id,
        out_path,
        new_size,
        archive_path,
    )


def _update_status(video_id: str, status: str, progress: float) -> None:
    with SessionLocal() as session:
        v = session.get(Video, video_id)
        if v:
            v.compress_status = status
            v.compress_progress = progress
            session.commit()


def _begin_tracking(new_jobs: int) -> None:
    global _batch_total_jobs, _batch_completed_jobs, _batch_failed_jobs
    if new_jobs <= 0:
        return
    if _queue.qsize() == 0 and _current_video_id is None:
        _batch_total_jobs = 0
        _batch_completed_jobs = 0
        _batch_failed_jobs = 0
    _batch_total_jobs += new_jobs
