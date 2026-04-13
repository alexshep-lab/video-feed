"""Batch contact-sheet (frame palette) generation.

Videos don't get a contact sheet during scan — it's generated lazily on first
WatchPage open. For a review workflow where the user wants to glance at a
palette before watching the whole clip, lazy generation is painful (every
first-click blocks for minutes on older WMV / long files).

This service mirrors ``converter.py``: single asyncio worker, queue, start/stop,
batch tracking. Each job resolves the *effective* playable source (converted MP4
if available, else original) and feeds it to ``generate_contact_sheet``, which
already uses NVDEC decode + CPU fallback via the common hwaccel helpers.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import Video
from .thumbnail import generate_contact_sheet

logger = logging.getLogger("videofeed.palette")

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_current_video_id: str | None = None
_batch_total_jobs = 0
_batch_completed_jobs = 0
_batch_failed_jobs = 0
_stop_requested = False


def _contact_sheet_path(video_id: str) -> Path:
    return get_settings().media_dir / "contact_sheets" / f"{video_id}.jpg"


def palette_exists(video_id: str) -> bool:
    path = _contact_sheet_path(video_id)
    return path.exists() and path.stat().st_size > 0


def _effective_source(video: Video) -> Path | None:
    """Prefer converted MP4 if conversion is complete — decodes faster and
    gives a palette of what the user actually watches."""
    if video.convert_status == "completed" and video.converted_path:
        converted = Path(video.converted_path)
        if converted.exists():
            return converted
    if video.original_path:
        p = Path(video.original_path)
        if p.exists():
            return p
    return None


def get_palette_status() -> dict:
    overall = 0.0
    if _batch_total_jobs > 0:
        overall = min(
            (_batch_completed_jobs + _batch_failed_jobs) / _batch_total_jobs * 100,
            100.0,
        )
    current_title = None
    if _current_video_id:
        with SessionLocal() as session:
            video = session.get(Video, _current_video_id)
            if video:
                current_title = video.original_filename
    return {
        "queue_size": _queue.qsize(),
        "current_video_id": _current_video_id,
        "current_video_title": current_title,
        "worker_running": _worker_task is not None and not _worker_task.done(),
        "batch_total_jobs": _batch_total_jobs,
        "batch_completed_jobs": _batch_completed_jobs,
        "batch_failed_jobs": _batch_failed_jobs,
        "overall_progress": round(overall, 1),
    }


def count_videos_without_palette(session: Session) -> int:
    """Rough count of non-deleted videos whose contact sheet file is missing.

    Walks IDs of candidate videos and stat()s each expected path. For very
    large libraries this is O(N) disk stat calls — cheap enough (~2-3 sec
    for 5000 rows on local SSD).
    """
    rows = session.execute(
        select(Video.id).where(Video.deleted_at.is_(None))
    ).all()
    media_dir = get_settings().media_dir / "contact_sheets"
    missing = 0
    for (vid,) in rows:
        p = media_dir / f"{vid}.jpg"
        try:
            if not p.exists() or p.stat().st_size == 0:
                missing += 1
        except OSError:
            missing += 1
    return missing


def enqueue_missing_palettes(session: Session) -> int:
    """Queue every non-deleted video that doesn't already have a contact sheet."""
    global _stop_requested
    _stop_requested = False

    rows = session.execute(
        select(Video.id).where(Video.deleted_at.is_(None))
    ).all()
    # Avoid re-queuing IDs already in the queue
    queued_ids: set[str] = set()
    try:
        queued_ids = {item for item in list(_queue._queue) if item != "__STOP__"}  # type: ignore[attr-defined]
    except Exception:
        pass
    if _current_video_id:
        queued_ids.add(_current_video_id)

    count = 0
    for (video_id,) in rows:
        if video_id in queued_ids:
            continue
        if palette_exists(video_id):
            continue
        _queue.put_nowait(video_id)
        count += 1

    _begin_tracking(count)
    return count


def enqueue_one(video_id: str) -> None:
    _begin_tracking(1)
    _queue.put_nowait(video_id)


def start_palette_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_palette_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _queue.put_nowait("__STOP__")
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _worker_task = None


def stop_palette_all() -> dict:
    """Drain the queue, set stop flag. There's no long-running subprocess to
    kill here — palette generation happens synchronously inside ffmpeg called
    via ``subprocess.run`` in ``thumbnail._run_ffmpeg_with_hw_fallback``.
    Those calls are shorter than compression so waiting for the current one
    to finish is acceptable.
    """
    global _batch_total_jobs, _stop_requested
    _stop_requested = True
    dropped = 0
    while True:
        try:
            _queue.get_nowait()
            _queue.task_done()
            dropped += 1
        except asyncio.QueueEmpty:
            break
    _batch_total_jobs = _batch_completed_jobs + _batch_failed_jobs + (1 if _current_video_id else 0)
    logger.warning("Palette STOP: dropped=%d interrupted=%s", dropped, _current_video_id)
    return {"dropped_queued": dropped, "interrupted_video_id": _current_video_id}


async def _worker_loop() -> None:
    global _current_video_id, _batch_completed_jobs, _batch_failed_jobs
    logger.info("Palette worker started")
    while True:
        video_id = await _queue.get()
        if video_id == "__STOP__":
            break
        if _stop_requested:
            # Additional guard — drop items pushed just before stop was pressed
            _queue.task_done()
            continue
        _current_video_id = video_id
        try:
            await asyncio.to_thread(_generate_one, video_id)
            _batch_completed_jobs += 1
        except Exception:
            logger.exception("Palette generation failed for %s", video_id)
            _batch_failed_jobs += 1
        finally:
            _current_video_id = None
            _queue.task_done()


def _generate_one(video_id: str) -> None:
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if not video:
            return
        source = _effective_source(video)
        duration = video.duration or 0
    if source is None:
        logger.warning("Palette skip %s: no playable source", video_id)
        raise FileNotFoundError(video_id)
    generate_contact_sheet(source, video_id, duration)


def _begin_tracking(new_jobs: int) -> None:
    global _batch_total_jobs, _batch_completed_jobs, _batch_failed_jobs
    if new_jobs <= 0:
        return
    if _queue.qsize() == 0 and _current_video_id is None:
        _batch_total_jobs = 0
        _batch_completed_jobs = 0
        _batch_failed_jobs = 0
    _batch_total_jobs += new_jobs
