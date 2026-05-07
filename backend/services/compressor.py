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
import time
from pathlib import Path

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import Video
from ._queue_tracking import QueuedIds
from .encoder import build_quality_video_args, get_effective_encoder
from .metadata import extract_video_metadata
from .proc_utils import HIDDEN_SUBPROCESS_KWARGS
from .thumbnail import generate_contact_sheet, generate_thumbnail, invalidate_video_cache

logger = logging.getLogger("videofeed.compressor")

_queue: asyncio.Queue[str] = asyncio.Queue()
_queued_ids = QueuedIds()
_worker_task: asyncio.Task | None = None
_current_video_id: str | None = None
_current_proc: asyncio.subprocess.Process | None = None
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
    _queued_ids.add(video_id)
    _queue.put_nowait(video_id)


def get_oversized_candidates(session: Session, min_height: int = 1440, force: bool = False) -> list[Video]:
    # Threshold is applied to the SHORTER side, not `height`. For portrait
    # videos `height` is the long axis, so the old `Video.height > min_height`
    # check flagged a 1080x1920 FHD vertical as a QHD+ candidate. The compressor
    # target is a 1920x1080 box regardless of orientation, so what matters is
    # whether the video's shorter side exceeds the box's shorter side.
    shorter_side = case(
        (Video.width > Video.height, Video.height),
        else_=Video.width,
    )
    statement = select(Video).where(
        Video.height.is_not(None),
        Video.width.is_not(None),
        shorter_side > min_height,
        Video.deleted_at.is_(None),
    )
    if force:
        statement = statement.where(Video.compress_status.not_in(["processing", "pending"]))
    else:
        statement = statement.where(Video.compress_status.in_(["none", "failed"]))

    candidates = session.scalars(statement).all()
    # Drop rows whose physical file is gone (e.g. user deleted manually).
    # Cheap stat() per row — for ~hundreds of candidates this is sub-second.
    return [v for v in candidates if v.original_path and Path(v.original_path).exists()]


def count_oversized_candidates(session: Session, min_height: int = 1440, force: bool = False) -> int:
    return len(get_oversized_candidates(session, min_height=min_height, force=force))


def enqueue_oversized(session: Session, min_height: int = 1440, force: bool = False) -> int:
    """Queue all oversized videos that are eligible for compression."""
    videos = get_oversized_candidates(session, min_height=min_height, force=force)
    queued_ids = _queued_ids.snapshot()
    if _current_video_id:
        queued_ids.add(_current_video_id)

    count = 0
    for v in videos:
        if v.id in queued_ids:
            continue
        _queued_ids.add(v.id)
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


def stop_compress_all() -> dict:
    """Stop everything: drain the queue, kill the active ffmpeg, reset tracking.

    The worker task itself stays alive and idle so subsequent enqueues
    still work. Returns counts of what was affected for the caller to
    show to the user.
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

    killed = False
    if _current_proc is not None and _current_proc.returncode is None:
        try:
            _current_proc.kill()
            killed = True
        except ProcessLookupError:
            pass

    # Keep completed + failed counts so the UI still reflects what was done,
    # but shrink the "total" so the progress bar doesn't sit at an impossible
    # intermediate value forever.
    _batch_total_jobs = _batch_completed_jobs + _batch_failed_jobs + (1 if _current_video_id else 0)

    interrupted_id = _current_video_id
    if interrupted_id:
        _update_status(interrupted_id, "failed", 0.0)

    logger.warning("Compress STOP: dropped=%d killed_current=%s interrupted=%s",
                   dropped, killed, interrupted_id)
    return {
        "dropped_queued": dropped,
        "killed_current": killed,
        "interrupted_video_id": interrupted_id,
    }


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
    global _current_video_id, _current_proc, _batch_completed_jobs, _batch_failed_jobs
    logger.info("Compress worker started")
    while True:
        video_id = await _queue.get()
        if video_id == "__STOP__":
            break
        _queued_ids.discard(video_id)
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
            _current_proc = None
            _queue.task_done()


async def _compress_video(video_id: str) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if not video:
            return
        src = Path(video.original_path)
        if not src.exists():
            # Source file disappeared (user deleted manually, etc.). Soft-delete the
            # row so it stops cluttering candidate lists; archived copy may still exist.
            from datetime import datetime, timezone
            video.deleted_at = datetime.now(timezone.utc)
            video.compress_status = "failed"
            video.compress_progress = 0.0
            session.commit()
            logger.warning(
                "Compress %s: source file missing (%s), soft-deleted DB row",
                video_id, src,
            )
            return
        duration = video.duration or 0
        src_width = video.width or 0
        src_height = video.height or 0

    out_path = build_compressed_output_path(src)
    archive_path = build_archive_path(src, settings.big_archive_dir)

    _update_status(video_id, "processing", 0.0)

    # Target box is 1920×1080 laid out by orientation. Unconditionally capping
    # height at 1080 would squash a portrait 2160×3840 source down to ~607×1080,
    # throwing away half the usable resolution. For portrait the box becomes
    # 1080×1920 (width capped at 1080, height at 1920). `force_original_aspect_ratio=decrease`
    # does the rest — the video is scaled to fit inside the box preserving AR.
    if src_height > src_width and src_width > 0:
        target_w, target_h = 1080, 1920  # portrait
    else:
        target_w, target_h = 1920, TARGET_HEIGHT  # landscape / square (default)

    encoder_args = build_quality_video_args(crf_or_cq=CRF, preset=PRESET)
    encoder_name = get_effective_encoder()
    cmd = [
        settings.ffmpeg_binary,
        "-hide_banner",
        "-loglevel", "error",
        "-nostats",
        "-progress", "pipe:2",
        "-i", str(src),
        "-vf", f"scale='min({target_w},iw)':'min({target_h},ih)':force_original_aspect_ratio=decrease",
        *encoder_args,
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        "-y",
        str(out_path),
    ]
    logger.info("Compressing %s using encoder=%s", video_id, encoder_name)

    global _current_proc
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **HIDDEN_SUBPROCESS_KWARGS,
    )
    _current_proc = proc

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
    _current_proc = None

    if proc.returncode != 0 or not out_path.exists():
        _update_status(video_id, "failed", 0.0)
        # Clean up partial output from a killed or failed run
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        return

    new_size = out_path.stat().st_size
    shutil.move(str(src), str(archive_path))

    try:
        new_metadata = extract_video_metadata(out_path)
    except Exception:
        new_metadata = {}

    with SessionLocal() as session:
        video = session.get(Video, video_id)
        if not video:
            return

        # Check whether the compressed output path is already owned by another
        # video row (happens when the FHD version had been imported as its own
        # entry before the source was compressed). Without this we'd hit
        # "UNIQUE constraint failed: videos.original_path" on UPDATE.
        target_collision = session.scalar(
            select(Video).where(
                Video.original_path == str(out_path),
                Video.id != video_id,
            )
        )

        if target_collision is not None:
            logger.warning(
                "Compress %s: target path %s already belongs to %s — merging into existing row",
                video_id, out_path, target_collision.id,
            )
            _merge_into_existing(session, video, target_collision, out_path, new_size, new_metadata)
            session.commit()
            logger.info(
                "Compressed %s -> %s (%d bytes, merged into %s)",
                video_id, out_path, new_size, target_collision.id,
            )
            return

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

        # Drop cached thumbnail / contact sheet / preview frames generated from
        # the *old* source file. Without this, ensure_frame() returns the stale
        # cached image because it short-circuits on `target.exists()`.
        invalidate_video_cache(video.id)
        try:
            thumbnail = generate_thumbnail(out_path, video.id, video.duration)
            video.thumbnail_path = str(thumbnail)
        except Exception:
            pass
        # Eagerly pre-generate the contact sheet now while we already have the
        # file in disk cache. Lazy generation on first WatchPage open is *very*
        # slow on remote drives because select-filter walks the entire file —
        # doing it here in the worker keeps user requests instant.
        try:
            generate_contact_sheet(out_path, video.id, video.duration)
        except Exception:
            logger.warning("Failed to pre-generate contact sheet for %s", video_id)
        session.commit()
    logger.info(
        "Compressed %s -> %s (%d bytes), archived original to %s",
        video_id,
        out_path,
        new_size,
        archive_path,
    )


def _merge_into_existing(
    session,
    source: Video,
    target: Video,
    out_path: Path,
    new_size: int,
    new_metadata: dict,
) -> None:
    """Fold a freshly-compressed video into a pre-existing row at the same path.

    Used when compressor's output filename collides with an already-indexed
    video record (e.g. user manually placed the FHD copy next to the 4K source
    earlier, and both got scanned as separate rows).

    Behavior:
      - Target's file metadata is refreshed (its file content was just rewritten by ffmpeg -y)
      - View counts, watch time, favorite/confirmed flags are merged from source
      - Tags from source are added to target
      - Source row is soft-deleted (its physical file is already in the archive)
    """
    from datetime import datetime, timezone

    # Refresh target metadata — its on-disk file was overwritten by the new compress output
    target.file_size = new_size
    try:
        target.file_mtime = out_path.stat().st_mtime
    except OSError:
        pass
    target.duration = new_metadata.get("duration", target.duration)
    target.width = new_metadata.get("width", target.width)
    target.height = new_metadata.get("height", target.height)
    target.codec = new_metadata.get("codec", target.codec)
    target.is_vertical = new_metadata.get("is_vertical", target.is_vertical)
    target.compress_status = "completed"
    target.compress_progress = 100.0
    target.compressed_path = str(out_path)
    target.compressed_size = new_size

    # Cherry-pick stats and flags from the source record
    target.view_count = max(target.view_count or 0, source.view_count or 0)
    target.total_watch_time = max(target.total_watch_time or 0.0, source.total_watch_time or 0.0)
    if source.last_watched_at and (
        not target.last_watched_at or source.last_watched_at > target.last_watched_at
    ):
        target.last_watched_at = source.last_watched_at
    if source.favorite:
        target.favorite = True
    if source.confirmed:
        target.confirmed = True

    # Merge tags (many-to-many)
    existing_tag_ids = {t.id for t in target.tag_objects}
    for tag in source.tag_objects:
        if tag.id not in existing_tag_ids:
            target.tag_objects.append(tag)

    # Refresh target derived assets since the on-disk file content changed.
    # Drop the stale cache first, then regenerate eagerly so the user doesn't
    # pay the contact-sheet generation cost on the next WatchPage open.
    invalidate_video_cache(target.id)
    try:
        thumbnail = generate_thumbnail(out_path, target.id, target.duration)
        target.thumbnail_path = str(thumbnail)
    except Exception:
        pass
    try:
        generate_contact_sheet(out_path, target.id, target.duration)
    except Exception:
        logger.warning("Failed to pre-generate contact sheet for merged %s", target.id)

    # Drop the source row's cached assets too — they're orphans now.
    invalidate_video_cache(source.id)

    # Soft-delete source row — its physical file is now in the archive
    source.deleted_at = datetime.now(timezone.utc)
    source.compress_status = "completed"
    source.compress_progress = 100.0


# ---- Archived originals (big_archive_dir) management --------------------
#
# Note on drive economics: after a successful compress we `shutil.move` the
# source into `big_archive_dir`. When the archive lives on the SAME physical
# drive as the library (the default), move() degenerates into a rename and
# frees zero bytes. The compressed FHD.mp4 is pure addition — the drive gets
# *fuller*, not freer. These helpers let the user actually reclaim that space
# by recycle-binning archived originals once they've decided the FHD copy is
# good enough.

def list_archive(archive_root: Path) -> dict:
    """Walk the archive root; return size summary + file list (oldest first)."""
    root_str = str(archive_root)
    if not archive_root.exists() or not archive_root.is_dir():
        return {
            "path": root_str,
            "exists": False,
            "total_size": 0,
            "file_count": 0,
            "items": [],
        }

    items: list[dict] = []
    total = 0
    now = time.time()
    for path in archive_root.rglob("*"):
        try:
            if not path.is_file():
                continue
            st = path.stat()
        except OSError:
            continue
        items.append({
            "path": str(path),
            "name": path.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "age_days": int(max(0, (now - st.st_mtime) // 86400)),
        })
        total += st.st_size

    items.sort(key=lambda it: it["mtime"])  # oldest first — easier to skim "what's safe to drop"
    return {
        "path": root_str,
        "exists": True,
        "total_size": total,
        "file_count": len(items),
        "items": items,
    }


def purge_archive(
    archive_root: Path,
    older_than_days: int | None = None,
    paths: list[str] | None = None,
) -> dict:
    """Recycle-bin archived originals.

    Selection rules (checked in order):
      - If ``paths`` is non-empty: only those explicit paths are recycled.
      - Else if ``older_than_days`` is set: files with mtime older than the
        cutoff are recycled.
      - Else: every file under ``archive_root`` is recycled.

    Every candidate must resolve to a path *inside* ``archive_root`` — this
    blocks path-traversal from the ``paths`` list. We also only touch regular
    files; directories are left alone (empty ones can be cleaned manually).
    """
    from .fileops import move_to_recycle_bin

    if not archive_root.exists() or not archive_root.is_dir():
        return {"recycled": 0, "failed": 0, "total_bytes_freed": 0, "errors": []}

    try:
        root_resolved = archive_root.resolve()
    except (OSError, RuntimeError):
        return {"recycled": 0, "failed": 0, "total_bytes_freed": 0, "errors": [
            {"path": str(archive_root), "error": "archive_root resolve failed"},
        ]}

    candidates: list[Path] = []
    if paths:
        for raw in paths:
            try:
                cand = Path(raw)
                cand_resolved = cand.resolve()
            except (OSError, RuntimeError):
                continue
            try:
                cand_resolved.relative_to(root_resolved)
            except ValueError:
                # Rejected: outside archive_root. Don't recycle.
                continue
            if cand_resolved.is_file():
                candidates.append(cand_resolved)
    else:
        cutoff: float | None = None
        if older_than_days is not None and older_than_days >= 0:
            cutoff = time.time() - older_than_days * 86400
        for path in archive_root.rglob("*"):
            try:
                if not path.is_file():
                    continue
                if cutoff is not None and path.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            candidates.append(path)

    recycled = 0
    failed = 0
    bytes_freed = 0
    errors: list[dict] = []
    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        try:
            move_to_recycle_bin(path)
            recycled += 1
            bytes_freed += size
        except Exception as exc:
            failed += 1
            errors.append({"path": str(path), "error": str(exc)})

    logger.info(
        "Archive purge: recycled=%d failed=%d freed=%d bytes (older_than_days=%s, explicit_paths=%d)",
        recycled, failed, bytes_freed, older_than_days, len(paths or []),
    )
    return {
        "recycled": recycled,
        "failed": failed,
        "total_bytes_freed": bytes_freed,
        "errors": errors[:30],
    }


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
