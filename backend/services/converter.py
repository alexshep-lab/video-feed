"""Browser-friendly conversion service.

Converts non-browser-playable formats (currently WMV) into MP4
(H.264 + AAC + +faststart) so they can play directly in <video>.

The original source file is *kept in place* — the converted output is
written to ``settings.converted_dir / {video_id}.mp4`` and remembered
on the Video row via ``converted_path``. The streaming endpoint then
prefers the converted file when ``convert_status == 'completed'``.

Architecture mirrors ``services.compressor``: a single asyncio worker
processes a queue, status is exposed via ``get_convert_status()``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import Video
from .encoder import build_hw_decode_args, build_quality_video_args, get_effective_encoder
from .thumbnail import generate_contact_sheet, generate_thumbnail, invalidate_video_cache

logger = logging.getLogger("videofeed.converter")

# Extensions that browsers cannot reliably play and that should be
# auto-converted to MP4 on import.
#
# .wmv: WMV1/WMV2/VC-1 video + WMA audio — no browser support
# .avi: usually Xvid/DivX/MS-MPEG4 + MP3 — no browser support. Some AVIs are
#       actually H.264-in-AVI, which browsers also don't play (wrong container),
#       so we still convert but to a cheap MP4 remux when possible.
NEEDS_CONVERSION_EXTENSIONS: set[str] = {".wmv", ".avi"}

# Video codecs that are already H.264-equivalent and don't need re-encode.
# When the source uses one of these but the container is wrong (e.g. H.264-in-AVI),
# we do a fast stream COPY into MP4 instead of a full re-encode — seconds per file.
REMUXABLE_VIDEO_CODECS: set[str] = {"h264", "avc1"}

# Quality target for the converted MP4. Slightly worse than the compressor
# because the goal is playability, not size reduction.
CRF = 23
PRESET = "medium"
AUDIO_BITRATE = "160k"

_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_current_video_id: str | None = None
_current_proc: asyncio.subprocess.Process | None = None
_batch_total_jobs = 0
_batch_completed_jobs = 0
_batch_failed_jobs = 0


def needs_conversion(path: str | Path) -> bool:
    """True if a file's extension is not browser-friendly."""
    return Path(path).suffix.lower() in NEEDS_CONVERSION_EXTENSIONS


def get_convert_status() -> dict:
    current_title = None
    current_progress = 0.0
    if _current_video_id:
        with SessionLocal() as session:
            video = session.get(Video, _current_video_id)
            if video:
                current_title = video.original_filename
                current_progress = video.convert_progress or 0.0

    overall_progress = 0.0
    if _batch_total_jobs > 0:
        overall_progress = min(
            (
                _batch_completed_jobs
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
        "encoder": get_effective_encoder(),
    }


def enqueue_convert(video_id: str) -> None:
    _begin_tracking(1)
    _queue.put_nowait(video_id)


def _ext_filter_clauses() -> list:
    """SQL clauses to match rows whose original_path ends with a needs-conversion extension."""
    return [Video.original_path.ilike(f"%{ext}") for ext in NEEDS_CONVERSION_EXTENSIONS]


def _base_pending_conversion_query():
    """Common WHERE clauses for "videos that still need conversion"."""
    from sqlalchemy import or_
    return select(Video).where(
        Video.deleted_at.is_(None),
        Video.convert_status.in_(["none", "pending", "failed"]),
        or_(*_ext_filter_clauses()),
    )


SORT_OPTIONS = {
    "h264_first": "h264 first (cheap remux), then by size ascending",
    "size_asc": "smallest first",
    "size_desc": "largest first",
    "name": "alphabetical",
}


def _apply_sort(statement, sort: str):
    if sort == "size_asc":
        return statement.order_by(Video.file_size.asc())
    if sort == "size_desc":
        return statement.order_by(Video.file_size.desc())
    if sort == "name":
        return statement.order_by(Video.original_filename.asc())
    # default: h264_first — h264 codec rows surface first, then by size ascending
    is_h264 = case((Video.codec == "h264", 0), else_=1)
    return statement.order_by(is_h264.asc(), Video.file_size.asc())


def count_pending_conversion_candidates(session: Session) -> int:
    """Total of pending-conversion candidates (no file existence check, fast)."""
    statement = select(func.count()).select_from(_base_pending_conversion_query().subquery())
    return session.scalar(statement) or 0


def query_pending_conversion_candidates(
    session: Session,
    limit: int = 20,
    offset: int = 0,
    sort: str = "h264_first",
) -> list[Video]:
    """Paginated, SQL-sorted candidate list for the maintenance UI.

    Skips the per-row file-existence check (which is expensive on slow drives).
    Stale rows are cleaned up automatically by the worker on first attempt.
    """
    statement = _apply_sort(_base_pending_conversion_query(), sort).offset(offset).limit(limit)
    return list(session.scalars(statement).all())


def get_pending_conversion_candidates(session: Session) -> list[Video]:
    """All videos whose original file needs conversion and that aren't done yet.

    This walks every candidate and stat()s its file. Used by the "convert all"
    workflow where we want to skip ghost rows. Slow on big libraries — prefer
    ``query_pending_conversion_candidates`` for UI listings.
    """
    statement = _base_pending_conversion_query()
    candidates: list[Video] = []
    for video in session.scalars(statement).all():
        if not video.original_path:
            continue
        if not Path(video.original_path).exists():
            continue
        candidates.append(video)
    return candidates


def enqueue_all_pending_conversions(session: Session) -> int:
    videos = get_pending_conversion_candidates(session)
    queued_ids = {item for item in list(_queue._queue) if item != "__STOP__"}  # type: ignore[attr-defined]
    if _current_video_id:
        queued_ids.add(_current_video_id)

    count = 0
    for v in videos:
        if v.id in queued_ids:
            continue
        _queue.put_nowait(v.id)
        v.convert_status = "pending"
        v.convert_progress = 0.0
        count += 1
    if count > 0:
        _begin_tracking(count)
    session.commit()
    return count


def start_convert_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(_worker_loop())


def stop_convert_all() -> dict:
    """Drain the queue, kill the active ffmpeg, reset batch tracking.

    Worker stays alive and idle for future enqueues. The partial output MP4
    (if any) is cleaned up inside ``_convert_video`` when the proc returns
    a non-zero code.
    """
    global _batch_total_jobs
    dropped = 0
    while True:
        try:
            _queue.get_nowait()
            _queue.task_done()
            dropped += 1
        except asyncio.QueueEmpty:
            break

    killed = False
    if _current_proc is not None and _current_proc.returncode is None:
        try:
            _current_proc.kill()
            killed = True
        except ProcessLookupError:
            pass

    _batch_total_jobs = _batch_completed_jobs + _batch_failed_jobs + (1 if _current_video_id else 0)

    interrupted_id = _current_video_id
    if interrupted_id:
        _update_status(interrupted_id, "failed", 0.0)

    logger.warning("Convert STOP: dropped=%d killed_current=%s interrupted=%s",
                   dropped, killed, interrupted_id)
    return {
        "dropped_queued": dropped,
        "killed_current": killed,
        "interrupted_video_id": interrupted_id,
    }


async def stop_convert_worker() -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        _queue.put_nowait("__STOP__")
        _worker_task.cancel()
        try:
            await asyncio.wait_for(_worker_task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _worker_task = None


async def _worker_loop() -> None:
    global _current_video_id, _current_proc, _batch_completed_jobs, _batch_failed_jobs
    logger.info("Convert worker started (encoder=%s)", get_effective_encoder())
    while True:
        video_id = await _queue.get()
        if video_id == "__STOP__":
            break
        _current_video_id = video_id
        try:
            await _convert_video(video_id)
            _batch_completed_jobs += 1
        except Exception:
            logger.exception("Convert failed for %s", video_id)
            _update_status(video_id, "failed", 0.0)
            _batch_failed_jobs += 1
        finally:
            _current_video_id = None
            _current_proc = None
            _queue.task_done()


async def _convert_video(video_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if not video:
            return
        src = Path(video.original_path)
        if not src.exists():
            # Source disappeared (user deleted manually). Soft-delete the row so
            # it stops cluttering candidate lists. Mirrors compressor behavior.
            from datetime import datetime, timezone
            video.deleted_at = datetime.now(timezone.utc)
            video.convert_status = "failed"
            video.convert_progress = 0.0
            session.commit()
            logger.warning(
                "Convert %s: source file missing (%s), soft-deleted DB row",
                video_id, src,
            )
            return
        duration = video.duration or 0
        codec = (video.codec or "").lower().strip()

        if not needs_conversion(src):
            # Source no longer needs conversion (e.g. compressed away). Mark skipped.
            video.convert_status = "skipped"
            video.convert_progress = 0.0
            session.commit()
            return

    settings.converted_dir.mkdir(parents=True, exist_ok=True)
    out_path = settings.converted_dir / f"{video_id}.mp4"
    if out_path.exists():
        try:
            out_path.unlink()
        except OSError:
            pass

    _update_status(video_id, "processing", 0.0)

    # Fast path: if source video stream is already H.264, just remux into MP4.
    # No re-encode needed — only the audio gets re-encoded to AAC for browser support.
    # This turns minutes-per-file into seconds-per-file for H.264-in-AVI cases.
    is_remux = codec in REMUXABLE_VIDEO_CODECS
    encoder_name = "stream-copy" if is_remux else get_effective_encoder()

    if is_remux:
        video_args = ["-c:v", "copy"]
        hw_decode_args: list[str] = []  # no decode happens during remux
    else:
        video_args = build_quality_video_args(crf_or_cq=CRF, preset=PRESET)
        hw_decode_args = build_hw_decode_args()

    def build_cmd(hw_args: list[str]) -> list[str]:
        return [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel", "error",
            "-nostats",
            "-progress", "pipe:2",
            *hw_args,
            "-i", str(src),
            *video_args,
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-ac", "2",
            "-movflags", "+faststart",
            "-y",
            str(out_path),
        ]

    logger.info(
        "Converting %s (%s, codec=%s) mode=%s hw_decode=%s -> %s",
        video_id, src.name, codec or "?", encoder_name, bool(hw_decode_args), out_path,
    )

    returncode = await _run_ffmpeg_with_progress(build_cmd(hw_decode_args), duration, video_id)

    # If HW decode failed (NVDEC may not support every WMV/VC-1 variant on Turing),
    # retry once on pure CPU decode. The encode path stays NVENC if available.
    if returncode != 0 and hw_decode_args:
        logger.warning(
            "Convert %s: HW decode failed (rc=%s), retrying with CPU decode",
            video_id, returncode,
        )
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        returncode = await _run_ffmpeg_with_progress(build_cmd([]), duration, video_id)

    if returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        logger.error("Conversion failed for %s (returncode=%s)", video_id, returncode)
        _update_status(video_id, "failed", 0.0)
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        return

    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if video:
            video.convert_status = "completed"
            video.convert_progress = 100.0
            video.converted_path = str(out_path)
            session.commit()

    # Drop cached thumbnail / contact sheet / preview frames that were built
    # from the original WMV/AVI (may not exist, or be from pre-conversion
    # ffprobe with weird codec metadata). Then eagerly regenerate them from
    # the fresh H.264 MP4 while the file is still hot in disk cache —
    # decoding this MP4 is much faster than decoding the original WMV on
    # first WatchPage open, and it's better to pay that cost here in the
    # worker than make the user wait.
    invalidate_video_cache(video_id)
    try:
        generate_thumbnail(out_path, video_id, duration)
    except Exception:
        logger.warning("Failed to regen thumbnail after convert for %s", video_id)
    try:
        generate_contact_sheet(out_path, video_id, duration)
    except Exception:
        logger.warning("Failed to pre-generate contact sheet after convert for %s", video_id)

    logger.info("Converted %s -> %s (%d bytes)", video_id, out_path, out_path.stat().st_size)


async def _run_ffmpeg_with_progress(
    cmd: list[str], duration: float, video_id: str
) -> int:
    """Run an ffmpeg command, parse `-progress pipe:2` output, push status updates.

    Returns the process return code. Used by the converter so we can retry the
    same logical job with different decode flags on failure.
    """
    global _current_proc
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _current_proc = proc
    try:
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
    finally:
        _current_proc = None
    return proc.returncode or 0


def _update_status(video_id: str, status: str, progress: float) -> None:
    with SessionLocal() as session:
        v = session.get(Video, video_id)
        if v:
            v.convert_status = status
            v.convert_progress = progress
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
