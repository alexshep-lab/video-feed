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
from ._queue_tracking import QueuedIds
from .encoder import build_hw_decode_args, build_quality_video_args, get_effective_encoder
from .proc_utils import HIDDEN_SUBPROCESS_KWARGS
from .thumbnail import generate_contact_sheet, generate_thumbnail, invalidate_video_cache

logger = logging.getLogger("videofeed.converter")

# Extensions that browsers cannot reliably play and that should be
# auto-converted to MP4 on import.
#
# .wmv: WMV1/WMV2/VC-1 video + WMA audio — no browser support
# .avi: usually Xvid/DivX/MS-MPEG4 + MP3 — no browser support. Some AVIs are
#       actually H.264-in-AVI, which browsers also don't play (wrong container),
#       so we still convert but to a cheap MP4 remux when possible.
# .flv: Sorenson Spark / VP6 / rarely H.264 — Flash is dead, browsers don't play it.
# .mpg/.mpeg: MPEG-1/MPEG-2 program streams — no browser decodes them.
# .asf: Advanced Systems Format, same family as WMV.
# .mts/.m2ts/.ts: MPEG-TS (AVCHD camcorders, DVB captures) — usually H.264 so
#       the remux path handles them cheaply, but the container itself is not
#       playable in <video>.
# .3gp: mobile container, usually H.263/AMR — needs re-encode.
NEEDS_CONVERSION_EXTENSIONS: set[str] = {
    ".wmv", ".avi",
    ".flv", ".mpg", ".mpeg", ".asf",
    ".mts", ".m2ts", ".ts", ".3gp",
}

# Video codecs that no browser can play natively, regardless of container.
# A file like `.mkv` containing wmv3 won't be caught by the extension filter
# above, so we also flag by codec when ffprobe metadata is available.
#
# - wmv1/wmv2/wmv3: Windows Media Video 7/8/9
# - vc1: VC-1 (Windows Media Video 9 Advanced Profile)
# - msmpeg4v1/v2/v3: Microsoft MPEG-4 variants (DivX ;-), etc.)
# - mpeg4: MPEG-4 Part 2 (Xvid/DivX) — browsers only support Part 10 (H.264)
# - flv1: Sorenson Spark (old Flash video)
# - vp6/vp6f/vp6a: On2 VP6 (Flash)
# - h263: older mobile codec (.3gp)
# - mpeg1video/mpeg2video: MPEG-1/2 — browsers never supported them
# - rv10/rv20/rv30/rv40: RealVideo
# - theora: Ogg Theora (dropped from modern browsers)
NEEDS_CONVERSION_CODECS: set[str] = {
    "wmv1", "wmv2", "wmv3", "vc1",
    "msmpeg4v1", "msmpeg4v2", "msmpeg4v3",
    "mpeg4",
    "flv1", "vp6", "vp6f", "vp6a",
    "h263",
    "mpeg1video", "mpeg2video",
    "rv10", "rv20", "rv30", "rv40",
    "theora",
}

# Video codecs that are already H.264-equivalent and don't need re-encode.
# When the source uses one of these but the container is wrong (e.g. H.264-in-AVI),
# we do a fast stream COPY into MP4 instead of a full re-encode — seconds per file.
REMUXABLE_VIDEO_CODECS: set[str] = {"h264", "avc1"}

# Quality target for the converted MP4. Slightly worse than the compressor
# because the goal is playability, not size reduction.
#
# Preset is "ultrafast" -> NVENC p1 (fastest). This is a batch import path
# converting hundreds of WMV/AVI for glance-review; p1 is ~40% faster than
# p3 and on low-quality WMV/VC-1 sources the visual difference is invisible.
# The compressor still uses the slower preset for archival recompression.
CRF = 23
PRESET = "ultrafast"
AUDIO_BITRATE = "160k"

# Two parallel workers saturate NVENC on RTX 2080 (supports ~3 concurrent
# sessions) while keeping headroom for HLS transcoding / compression if the
# user kicks those off in parallel. Remux jobs are GPU-free so they coexist
# fine with encode jobs on the second worker.
CONCURRENCY = 2

_queue: asyncio.Queue[str] = asyncio.Queue()
_queued_ids = QueuedIds()
_worker_tasks: list[asyncio.Task] = []
# Per-worker active job state. Keyed by worker index; value is
# {"video_id": str, "proc": Process | None}. Only workers that currently hold
# a job appear in the dict.
_active: dict[int, dict] = {}
_batch_total_jobs = 0
_batch_completed_jobs = 0
_batch_failed_jobs = 0


def needs_conversion(path: str | Path, codec: str | None = None) -> bool:
    """True if the file isn't browser-playable.

    Matches by extension (fast, always available) OR by video codec when
    metadata is known. The codec check catches cases like wmv3-in-mkv that
    the extension filter alone would miss.
    """
    if Path(path).suffix.lower() in NEEDS_CONVERSION_EXTENSIONS:
        return True
    if codec and codec.lower().strip() in NEEDS_CONVERSION_CODECS:
        return True
    return False


def get_convert_status() -> dict:
    # Collect per-job state. Frontend still expects single `current_*` fields;
    # we fill those from the first active job and expose the full list as
    # `active_jobs` for callers that want richer UI later.
    active_jobs: list[dict] = []
    progress_sum = 0.0
    if _active:
        with SessionLocal() as session:
            for worker_id in sorted(_active):
                vid = _active[worker_id]["video_id"]
                video = session.get(Video, vid)
                progress = (video.convert_progress or 0.0) if video else 0.0
                title = video.original_filename if video else None
                progress_sum += progress
                active_jobs.append({
                    "worker_id": worker_id,
                    "video_id": vid,
                    "video_title": title,
                    "progress": round(progress, 1),
                })

    current_video_id = active_jobs[0]["video_id"] if active_jobs else None
    current_title = active_jobs[0]["video_title"] if active_jobs else None
    current_progress = active_jobs[0]["progress"] if active_jobs else 0.0

    overall_progress = 0.0
    if _batch_total_jobs > 0:
        overall_progress = min(
            (_batch_completed_jobs + progress_sum / 100) / _batch_total_jobs * 100,
            100.0,
        )

    worker_running = any(t for t in _worker_tasks if not t.done())

    return {
        "queue_size": _queue.qsize(),
        "current_video_id": current_video_id,
        "current_video_title": current_title,
        "current_progress": current_progress,
        "active_jobs": active_jobs,
        "concurrency": CONCURRENCY,
        "worker_running": worker_running,
        "batch_total_jobs": _batch_total_jobs,
        "batch_completed_jobs": _batch_completed_jobs,
        "batch_failed_jobs": _batch_failed_jobs,
        "overall_progress": round(overall_progress, 1),
        "encoder": get_effective_encoder(),
    }


def enqueue_convert(video_id: str) -> None:
    _begin_tracking(1)
    _queued_ids.add(video_id)
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
        or_(
            *_ext_filter_clauses(),
            func.lower(func.trim(Video.codec)).in_(NEEDS_CONVERSION_CODECS),
        ),
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
    # Process h264 sources first — they go through stream-copy remux (seconds per
    # file) rather than full re-encode. Putting them ahead of WMV means the user
    # sees most of their library become playable quickly, instead of waiting out
    # a long queue of heavy WMV transcodes before the fast ones get a turn.
    videos = list(session.scalars(_apply_sort(_base_pending_conversion_query(), "h264_first")).all())
    queued_ids = _queued_ids.snapshot()
    for slot in _active.values():
        queued_ids.add(slot["video_id"])

    count = 0
    for v in videos:
        if v.id in queued_ids:
            continue
        _queued_ids.add(v.id)
        _queue.put_nowait(v.id)
        v.convert_status = "pending"
        v.convert_progress = 0.0
        count += 1
    if count > 0:
        _begin_tracking(count)
    session.commit()
    return count


def start_convert_worker() -> None:
    # Prune finished tasks (e.g. after a previous run crashed) then spin up
    # however many more we need to reach CONCURRENCY.
    global _worker_tasks
    _worker_tasks = [t for t in _worker_tasks if not t.done()]
    while len(_worker_tasks) < CONCURRENCY:
        worker_id = len(_worker_tasks)
        _worker_tasks.append(asyncio.create_task(_worker_loop(worker_id)))


def stop_convert_all() -> dict:
    """Drain the queue, kill any active ffmpegs, reset batch tracking.

    Workers stay alive and idle for future enqueues. Partial output MPs
    are cleaned up inside ``_convert_video`` when the proc returns non-zero.
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
    _queued_ids.clear()

    killed = 0
    interrupted_ids: list[str] = []
    for slot in list(_active.values()):
        proc = slot.get("proc")
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                killed += 1
            except ProcessLookupError:
                pass
        vid = slot["video_id"]
        interrupted_ids.append(vid)
        _update_status(vid, "failed", 0.0)

    _batch_total_jobs = _batch_completed_jobs + _batch_failed_jobs + len(_active)

    logger.warning("Convert STOP: dropped=%d killed=%d interrupted=%s",
                   dropped, killed, interrupted_ids)
    return {
        "dropped_queued": dropped,
        "killed_current": killed > 0,
        "killed_count": killed,
        "interrupted_video_id": interrupted_ids[0] if interrupted_ids else None,
        "interrupted_video_ids": interrupted_ids,
    }


async def stop_convert_worker() -> None:
    global _worker_tasks
    alive = [t for t in _worker_tasks if not t.done()]
    for _ in alive:
        _queue.put_nowait("__STOP__")
    for t in alive:
        t.cancel()
    for t in alive:
        try:
            await asyncio.wait_for(t, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _worker_tasks = []


async def _worker_loop(worker_id: int) -> None:
    global _batch_completed_jobs, _batch_failed_jobs
    logger.info("Convert worker %d started (encoder=%s)", worker_id, get_effective_encoder())
    while True:
        video_id = await _queue.get()
        if video_id == "__STOP__":
            _queue.task_done()
            break
        _queued_ids.discard(video_id)
        _active[worker_id] = {"video_id": video_id, "proc": None}
        try:
            await _convert_video(worker_id, video_id)
            _batch_completed_jobs += 1
        except Exception:
            logger.exception("Convert failed for %s (worker %d)", video_id, worker_id)
            _update_status(video_id, "failed", 0.0)
            _batch_failed_jobs += 1
        finally:
            _active.pop(worker_id, None)
            _queue.task_done()


async def _convert_video(worker_id: int, video_id: str) -> None:
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

        if not needs_conversion(src, codec):
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

    returncode = await _run_ffmpeg_with_progress(worker_id, build_cmd(hw_decode_args), duration, video_id)

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
        returncode = await _run_ffmpeg_with_progress(worker_id, build_cmd([]), duration, video_id)

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
    worker_id: int, cmd: list[str], duration: float, video_id: str
) -> int:
    """Run an ffmpeg command, parse `-progress pipe:2` output, push status updates.

    Returns the process return code. Used by the converter so we can retry the
    same logical job with different decode flags on failure.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **HIDDEN_SUBPROCESS_KWARGS,
    )
    slot = _active.get(worker_id)
    if slot is not None:
        slot["proc"] = proc
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
        slot = _active.get(worker_id)
        if slot is not None:
            slot["proc"] = None
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
    if _queue.qsize() == 0 and not _active:
        _batch_total_jobs = 0
        _batch_completed_jobs = 0
        _batch_failed_jobs = 0
    _batch_total_jobs += new_jobs
