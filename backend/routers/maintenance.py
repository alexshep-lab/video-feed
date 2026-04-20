"""Maintenance endpoints: duplicates, compression, contact sheets, etc."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.compressor import (
    build_compressed_output_path,
    count_oversized_candidates,
    enqueue_compress,
    enqueue_oversized,
    get_oversized_candidates,
    get_compress_status,
    list_archive,
    purge_archive,
    start_compress_worker,
    stop_compress_all,
)
from ..services.converter import (
    SORT_OPTIONS,
    count_pending_conversion_candidates,
    enqueue_all_pending_conversions,
    enqueue_convert,
    get_convert_status,
    needs_conversion,
    query_pending_conversion_candidates,
    start_convert_worker,
    stop_convert_all,
)
from ..services.duplicates import (
    compute_all_phashes,
    find_phash_duplicates,
    find_size_duration_duplicates,
)
from ..services.encoder import detect_nvenc_available, get_effective_encoder
from ..services.palette import (
    SORT_OPTIONS as PALETTE_SORT_OPTIONS,
    count_videos_without_palette,
    enqueue_batch as enqueue_palette_batch,
    enqueue_missing_palettes,
    enqueue_one as enqueue_palette_one,
    get_palette_status,
    list_missing_palette_videos,
    start_palette_worker,
    stop_palette_all,
)
from .videos import to_list_item


router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("/duplicates/exact")
def get_exact_duplicates(request: Request, db: Session = Depends(get_db)) -> dict:
    """Find videos with same file_size + duration."""
    groups = find_size_duration_duplicates(db)
    return {
        "count": len(groups),
        "groups": [
            [to_list_item(request, v).model_dump() for v in g]
            for g in groups
        ],
    }


@router.get("/duplicates/perceptual")
def get_perceptual_duplicates(
    request: Request,
    threshold: int = 5,
    db: Session = Depends(get_db),
) -> dict:
    """Find videos with similar thumbnail hash (perceptual)."""
    groups = find_phash_duplicates(db, threshold=threshold)
    return {
        "count": len(groups),
        "groups": [
            [to_list_item(request, v).model_dump() for v in g]
            for g in groups
        ],
    }


@router.post("/duplicates/compute-hashes")
def compute_hashes(db: Session = Depends(get_db)) -> dict:
    """Compute phash for all videos missing one. Run this once after import."""
    count = compute_all_phashes(db, only_missing=True)
    return {"status": "ok", "computed": count}


# ---- Compression ----

@router.post("/compress/{video_id}")
def compress_one(video_id: str, db: Session = Depends(get_db)) -> dict:
    from ..models import Video
    video = db.get(Video, video_id)
    if not video:
        return {"status": "not_found"}
    video.compress_status = "pending"
    db.commit()
    enqueue_compress(video_id)
    start_compress_worker()
    return {"status": "queued", "video_id": video_id}


@router.post("/compress/{video_id}/ignore")
def ignore_compress(video_id: str, db: Session = Depends(get_db)) -> dict:
    from ..models import Video

    video = db.get(Video, video_id)
    if not video:
        return {"status": "not_found"}
    video.compress_status = "ignored"
    video.compress_progress = 0.0
    db.commit()
    return {"status": "ignored", "video_id": video_id}


@router.post("/compress/oversized")
def compress_oversized(
    min_height: int = 1440,
    force: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Queue all videos with height >= min_height for compression."""
    eligible = count_oversized_candidates(db, min_height=min_height, force=force)
    count = enqueue_oversized(db, min_height=min_height, force=force)
    start_compress_worker()
    return {
        "status": "queued",
        "count": count,
        "eligible": eligible,
        "min_height": min_height,
        "force": force,
    }


@router.get("/compress/candidates")
def compress_candidates(min_height: int = 1440, force: bool = False, db: Session = Depends(get_db)) -> dict:
    return {
        "eligible": count_oversized_candidates(db, min_height=min_height, force=force),
        "min_height": min_height,
        "force": force,
    }


@router.get("/compress/candidates/list")
def compress_candidate_list(
    request: Request,
    min_height: int = 1440,
    force: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
) -> dict:
    videos = get_oversized_candidates(db, min_height=min_height, force=force)[: max(1, min(limit, 500))]
    return {
        "eligible": count_oversized_candidates(db, min_height=min_height, force=force),
        "items": [
            {
                **to_list_item(request, video).model_dump(),
                "target_filename": build_compressed_output_path(video.original_path).name,
            }
            for video in videos
        ],
        "min_height": min_height,
        "force": force,
    }


@router.get("/compress/status")
def compress_queue_status() -> dict:
    return get_compress_status()


@router.post("/compress/stop")
def compress_stop() -> dict:
    """Abort the active compression and drop everything queued."""
    return stop_compress_all()


# ---- Archived originals (space reclamation after compress) ----
#
# After compression the source is moved to ``big_archive_dir`` for safety. On
# a single-drive setup this frees no space — these endpoints let the user
# inspect and recycle-bin those archived originals once the FHD copies have
# been validated.

@router.get("/compress/archive")
def compress_archive_list() -> dict:
    """Files currently sitting in ``big_archive_dir`` (oldest first)."""
    from ..config import get_settings
    return list_archive(get_settings().big_archive_dir)


@router.post("/compress/archive/purge")
def compress_archive_purge(
    older_than_days: int | None = Body(default=None),
    paths: list[str] | None = Body(default=None),
) -> dict:
    """Recycle-bin archived originals.

    Body semantics:
      - ``paths`` — explicit list of absolute paths to recycle. Entries outside
        ``big_archive_dir`` are silently rejected (defense in depth).
      - ``older_than_days`` — applied only when ``paths`` is empty/null; every
        file older than the cutoff is recycled.
      - Both null → purge the entire archive.
    """
    from ..config import get_settings
    return purge_archive(
        get_settings().big_archive_dir,
        older_than_days=older_than_days,
        paths=paths,
    )


# ---- Browser-friendly conversion (e.g. WMV -> MP4) ----

@router.get("/convert/status")
def convert_queue_status() -> dict:
    return get_convert_status()


@router.get("/convert/candidates")
def convert_candidates(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    sort: str = "h264_first",
    db: Session = Depends(get_db),
) -> dict:
    """Paginated list of videos waiting for browser-friendly conversion.

    `sort` options: see `SORT_OPTIONS` in services.converter — by default
    h264 sources surface first because they go through the cheap remux fast-path.
    """
    if sort not in SORT_OPTIONS:
        sort = "h264_first"
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    total = count_pending_conversion_candidates(db)
    videos = query_pending_conversion_candidates(db, limit=limit, offset=offset, sort=sort)
    return {
        "total": total,
        "eligible": total,  # kept for backward-compat with older clients
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "sort_options": SORT_OPTIONS,
        "items": [to_list_item(request, v).model_dump() for v in videos],
    }


# NOTE: order matters — `/convert/all` and `/convert/queue` must be declared BEFORE
# `/convert/{video_id}`, otherwise FastAPI matches them as video_id="all"/"queue".
@router.post("/convert/all")
def convert_all_pending(db: Session = Depends(get_db)) -> dict:
    count = enqueue_all_pending_conversions(db)
    start_convert_worker()
    return {"status": "queued", "count": count}


@router.post("/convert/stop")
def convert_stop() -> dict:
    """Abort the active conversion and drop everything queued."""
    return stop_convert_all()


@router.post("/convert/queue")
def convert_batch(
    video_ids: list[str] = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> dict:
    """Enqueue an explicit list of video IDs in the order they were given.

    Used by the maintenance UI when the user picks specific candidates and wants
    them processed in a chosen order rather than the default DB-order batch.
    """
    from ..models import Video
    queued = 0
    skipped: list[dict] = []
    for vid in video_ids:
        video = db.get(Video, vid)
        if not video:
            skipped.append({"id": vid, "reason": "not_found"})
            continue
        if not needs_conversion(video.original_path, video.codec):
            skipped.append({"id": vid, "reason": "not_applicable"})
            continue
        if video.convert_status == "completed":
            skipped.append({"id": vid, "reason": "already_completed"})
            continue
        video.convert_status = "pending"
        video.convert_progress = 0.0
        enqueue_convert(vid)
        queued += 1
    db.commit()
    if queued:
        start_convert_worker()
    return {"status": "queued", "queued": queued, "skipped": skipped}


@router.post("/convert/{video_id}")
def convert_one(video_id: str, db: Session = Depends(get_db)) -> dict:
    from ..models import Video
    video = db.get(Video, video_id)
    if not video:
        return {"status": "not_found"}
    if not needs_conversion(video.original_path, video.codec):
        return {"status": "not_applicable", "reason": "source already browser-friendly"}
    video.convert_status = "pending"
    video.convert_progress = 0.0
    db.commit()
    enqueue_convert(video_id)
    start_convert_worker()
    return {"status": "queued", "video_id": video_id}


# ---- Video palettes / contact sheets ----

@router.get("/palettes/status")
def palette_status() -> dict:
    return get_palette_status()


@router.get("/palettes/missing-count")
def palette_missing_count(db: Session = Depends(get_db)) -> dict:
    return {"missing": count_videos_without_palette(db)}


@router.get("/palettes/failures")
def palette_failures(db: Session = Depends(get_db), limit: int = 500) -> dict:
    """Videos whose last palette-generation attempt raised. Cleared on success."""
    from ..models import Video
    rows = db.scalars(
        select(Video)
        .where(Video.palette_error.is_not(None), Video.deleted_at.is_(None))
        .order_by(Video.palette_failed_at.desc().nullslast())
        .limit(max(1, min(limit, 5000)))
    ).all()
    return {
        "total": len(rows),
        "items": [
            {
                "id": v.id,
                "title": v.title,
                "original_filename": v.original_filename,
                "original_path": v.original_path,
                "failed_at": v.palette_failed_at.isoformat() if v.palette_failed_at else None,
                "error": v.palette_error,
            }
            for v in rows
        ],
    }


@router.get("/palettes/candidates")
def palette_candidates(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    sort: str = "name",
    db: Session = Depends(get_db),
) -> dict:
    """Paginated list of videos that don't yet have a contact sheet."""
    if sort not in PALETTE_SORT_OPTIONS:
        sort = "name"
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    total, videos = list_missing_palette_videos(db, limit=limit, offset=offset, sort=sort)
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "sort_options": PALETTE_SORT_OPTIONS,
        "items": [to_list_item(request, v).model_dump() for v in videos],
    }


# NOTE: order matters — `/palettes/generate-all` and `/palettes/generate/queue`
# must precede `/palettes/generate/{video_id}`, otherwise FastAPI matches them
# as video_id="all"/"queue".
@router.post("/palettes/generate-all")
def palette_generate_all(db: Session = Depends(get_db)) -> dict:
    count = enqueue_missing_palettes(db)
    start_palette_worker()
    return {"status": "queued", "count": count}


@router.post("/palettes/generate/queue")
def palette_generate_batch(
    video_ids: list[str] = Body(..., embed=True),
) -> dict:
    """Enqueue an explicit list of IDs in the order given — ‘process selected first’."""
    queued = enqueue_palette_batch(video_ids)
    if queued:
        start_palette_worker()
    return {"status": "queued", "queued": queued, "requested": len(video_ids)}


@router.post("/palettes/generate/{video_id}")
def palette_generate_one(video_id: str, db: Session = Depends(get_db)) -> dict:
    from ..models import Video
    video = db.get(Video, video_id)
    if not video:
        return {"status": "not_found"}
    enqueue_palette_one(video_id)
    start_palette_worker()
    return {"status": "queued", "video_id": video_id}


@router.post("/palettes/stop")
def palette_stop() -> dict:
    return stop_palette_all()


# ---- Locked / orphaned files (soft-deleted DB row, file still on disk) ----

@router.get("/orphans")
def list_orphans(request: Request, db: Session = Depends(get_db)) -> dict:
    """Soft-deleted videos whose source file is still on disk.

    Happens when recycle-to-bin failed (file locked by an active stream / open
    in another app) — we soft-deleted the row as a fallback, but the file
    never moved. Listed here so the user can retry once the lock is released.
    """
    import os
    from ..models import Video as _V
    rows = db.query(_V).filter(_V.deleted_at.is_not(None)).order_by(_V.deleted_at.desc()).limit(500).all()
    items = []
    for v in rows:
        try:
            exists = bool(v.original_path) and os.path.exists(v.original_path)
        except Exception:
            exists = False
        if not exists:
            continue
        d = to_list_item(request, v).model_dump()
        d["deleted_at"] = v.deleted_at.isoformat() if v.deleted_at else None
        items.append(d)
    return {"count": len(items), "items": items}


@router.post("/orphans/{video_id}/retry")
def retry_orphan(video_id: str, db: Session = Depends(get_db)) -> dict:
    """Retry move-to-recycle for a soft-deleted row whose file is still on disk."""
    import os
    from pathlib import Path as _P
    from ..models import Video as _V
    from ..services.fileops import move_to_recycle_bin

    v = db.get(_V, video_id)
    if v is None:
        return {"status": "not_found"}
    if not v.original_path or not os.path.exists(v.original_path):
        # Nothing left on disk — purge the DB row fully.
        from ..models import WatchEvent, WatchProgress
        db.query(WatchEvent).filter(WatchEvent.video_id == video_id).delete(synchronize_session=False)
        db.query(WatchProgress).filter(WatchProgress.video_id == video_id).delete(synchronize_session=False)
        db.delete(v)
        db.commit()
        return {"status": "purged_no_file"}
    try:
        move_to_recycle_bin(_P(v.original_path))
    except Exception as e:
        return {"status": "still_locked", "error": str(e)}
    from ..models import WatchEvent, WatchProgress
    db.query(WatchEvent).filter(WatchEvent.video_id == video_id).delete(synchronize_session=False)
    db.query(WatchProgress).filter(WatchProgress.video_id == video_id).delete(synchronize_session=False)
    db.delete(v)
    db.commit()
    return {"status": "recycled"}


@router.post("/orphans/retry-all")
def retry_orphans_all(db: Session = Depends(get_db)) -> dict:
    """Retry move-to-recycle for every soft-deleted row whose file is still on disk."""
    import os
    from pathlib import Path as _P
    from ..models import Video as _V, WatchEvent, WatchProgress
    from ..services.fileops import move_to_recycle_bin

    rows = db.query(_V).filter(_V.deleted_at.is_not(None)).all()
    recycled = 0
    still_locked = 0
    purged_no_file = 0
    for v in rows:
        if not v.original_path:
            continue
        try:
            exists = os.path.exists(v.original_path)
        except Exception:
            exists = False
        if not exists:
            db.query(WatchEvent).filter(WatchEvent.video_id == v.id).delete(synchronize_session=False)
            db.query(WatchProgress).filter(WatchProgress.video_id == v.id).delete(synchronize_session=False)
            db.delete(v)
            purged_no_file += 1
            continue
        try:
            move_to_recycle_bin(_P(v.original_path))
            db.query(WatchEvent).filter(WatchEvent.video_id == v.id).delete(synchronize_session=False)
            db.query(WatchProgress).filter(WatchProgress.video_id == v.id).delete(synchronize_session=False)
            db.delete(v)
            recycled += 1
        except Exception:
            still_locked += 1
    db.commit()
    return {"recycled": recycled, "still_locked": still_locked, "purged_no_file": purged_no_file}


# ---- Missing-file cleanup (DB row exists, source file gone) ----

@router.get("/missing-files")
def list_missing_files(request: Request, db: Session = Depends(get_db)) -> dict:
    """Non-deleted videos whose original_path no longer exists on disk.

    Happens when files are moved/deleted outside the app. The records are
    useless — playback, palette generation, conversion all fail.
    """
    import os
    from ..models import Video as _V
    rows = db.scalars(select(_V).where(_V.deleted_at.is_(None))).all()
    items = []
    for v in rows:
        try:
            exists = bool(v.original_path) and os.path.exists(v.original_path)
        except Exception:
            exists = False
        if exists:
            continue
        items.append({
            "id": v.id,
            "title": v.title,
            "original_filename": v.original_filename,
            "original_path": v.original_path,
        })
    return {"count": len(items), "items": items}


@router.post("/missing-files/purge")
def purge_missing_files(db: Session = Depends(get_db)) -> dict:
    """Hard-delete DB rows whose source file is gone. Also drops derived assets."""
    import os
    from ..models import Video as _V, WatchEvent, WatchProgress
    from ..services.thumbnail import invalidate_video_cache

    rows = db.scalars(select(_V).where(_V.deleted_at.is_(None))).all()
    purged = 0
    for v in rows:
        try:
            exists = bool(v.original_path) and os.path.exists(v.original_path)
        except Exception:
            exists = False
        if exists:
            continue
        try:
            invalidate_video_cache(v.id)
        except Exception:
            pass
        db.query(WatchEvent).filter(WatchEvent.video_id == v.id).delete(synchronize_session=False)
        db.query(WatchProgress).filter(WatchProgress.video_id == v.id).delete(synchronize_session=False)
        db.delete(v)
        purged += 1
    db.commit()
    return {"purged": purged}


# ---- Short-video cleanup (duration <= threshold, move to Recycle Bin) ----

@router.get("/short-videos")
def list_short_videos(
    max_seconds: float = Query(default=420.0, ge=1.0),
    db: Session = Depends(get_db),
) -> dict:
    """Preview of active videos whose duration is <= max_seconds (default 7 min).

    NULL durations are skipped — we only touch rows where duration is known.
    """
    from ..models import Video as _V
    rows = db.scalars(
        select(_V).where(
            _V.deleted_at.is_(None),
            _V.duration.is_not(None),
            _V.duration <= max_seconds,
        )
    ).all()
    items = [
        {
            "id": v.id,
            "title": v.title,
            "original_filename": v.original_filename,
            "original_path": v.original_path,
            "duration": v.duration,
            "file_size": v.file_size,
        }
        for v in rows
    ]
    return {"count": len(items), "max_seconds": max_seconds, "items": items}


@router.post("/short-videos/purge")
def purge_short_videos(
    max_seconds: float = Query(default=420.0, ge=1.0),
    db: Session = Depends(get_db),
) -> dict:
    """Move files with duration <= max_seconds to Recycle Bin and hard-delete rows.

    NULL durations are skipped. Files that can't be moved (locked, permission)
    leave the DB row untouched so a retry can pick them up.
    """
    import os
    from pathlib import Path as _P
    from ..models import Video as _V, WatchEvent, WatchProgress
    from ..services.fileops import move_to_recycle_bin
    from ..services.thumbnail import invalidate_video_cache

    rows = db.scalars(
        select(_V).where(
            _V.deleted_at.is_(None),
            _V.duration.is_not(None),
            _V.duration <= max_seconds,
        )
    ).all()

    recycled = 0
    still_locked = 0
    purged_no_file = 0
    errors: list[dict] = []

    for v in rows:
        path_str = v.original_path or ""
        try:
            exists = bool(path_str) and os.path.exists(path_str)
        except Exception:
            exists = False

        if not exists:
            try:
                invalidate_video_cache(v.id)
            except Exception:
                pass
            db.query(WatchEvent).filter(WatchEvent.video_id == v.id).delete(synchronize_session=False)
            db.query(WatchProgress).filter(WatchProgress.video_id == v.id).delete(synchronize_session=False)
            db.delete(v)
            purged_no_file += 1
            continue

        try:
            move_to_recycle_bin(_P(path_str))
        except Exception as e:
            still_locked += 1
            errors.append({"id": v.id, "path": path_str, "error": str(e)})
            continue

        try:
            invalidate_video_cache(v.id)
        except Exception:
            pass
        db.query(WatchEvent).filter(WatchEvent.video_id == v.id).delete(synchronize_session=False)
        db.query(WatchProgress).filter(WatchProgress.video_id == v.id).delete(synchronize_session=False)
        db.delete(v)
        recycled += 1

    db.commit()
    return {
        "recycled": recycled,
        "still_locked": still_locked,
        "purged_no_file": purged_no_file,
        "max_seconds": max_seconds,
        "errors": errors[:20],
    }


# ---- Replace converted originals (move MP4 into library, Recycle WMV/AVI) ----

@router.get("/converted-originals")
def list_converted_originals(db: Session = Depends(get_db)) -> dict:
    """Active rows where a converted MP4 exists and the original is a WMV/AVI.

    These are candidates for ``POST .../replace``: moving the MP4 into the
    library folder (as ``<stem>.mp4``), Recycle-binning the WMV/AVI, and
    flattening the row so ``original_path`` points at the in-library MP4.
    """
    import os
    from ..models import Video as _V
    rows = db.scalars(
        select(_V).where(
            _V.deleted_at.is_(None),
            _V.convert_status == "completed",
            _V.converted_path.is_not(None),
        )
    ).all()
    items: list[dict] = []
    total_reclaimable = 0
    for v in rows:
        if not v.original_path or not v.converted_path:
            continue
        try:
            orig_exists = os.path.exists(v.original_path)
            conv_exists = os.path.exists(v.converted_path)
        except Exception:
            continue
        if not (orig_exists and conv_exists):
            continue
        if not needs_conversion(v.original_path, v.codec):
            continue
        try:
            orig_size = os.path.getsize(v.original_path)
        except OSError:
            orig_size = v.file_size or 0
        total_reclaimable += orig_size
        items.append({
            "id": v.id,
            "title": v.title,
            "original_path": v.original_path,
            "original_size": orig_size,
            "converted_path": v.converted_path,
        })
    return {"count": len(items), "reclaimable_bytes": total_reclaimable, "items": items}


@router.post("/converted-originals/replace")
def replace_converted_originals(db: Session = Depends(get_db)) -> dict:
    """Move the converted MP4 into the library next to the original, then
    Recycle-bin the WMV/AVI. Row becomes a normal MP4 row.

    Order is chosen to fail safe: move first (if it fails we've changed
    nothing), only delete the original after the move completes.
    """
    import os
    import shutil
    from pathlib import Path as _P
    from ..models import Video as _V
    from ..services.fileops import move_to_recycle_bin

    rows = db.scalars(
        select(_V).where(
            _V.deleted_at.is_(None),
            _V.convert_status == "completed",
            _V.converted_path.is_not(None),
        )
    ).all()

    replaced = 0
    skipped_collision = 0
    move_failed = 0
    recycle_failed = 0
    errors: list[dict] = []

    for v in rows:
        if not v.original_path or not v.converted_path:
            continue
        orig = _P(v.original_path)
        conv = _P(v.converted_path)
        if not orig.exists() or not conv.exists():
            continue
        if not needs_conversion(orig, v.codec):
            continue

        target = orig.with_suffix(".mp4")
        # If an `.mp4` with the same stem already exists next to the source
        # (pre-existing transcode, or stem collision with an unrelated file),
        # skip to avoid stomping on it.
        if target.exists() and target.resolve() != conv.resolve():
            skipped_collision += 1
            errors.append({"id": v.id, "reason": "target_exists", "target": str(target)})
            continue

        try:
            shutil.move(str(conv), str(target))
        except Exception as e:
            move_failed += 1
            errors.append({"id": v.id, "reason": "move_failed", "error": str(e)})
            continue

        # Move done. Now Recycle the original — if this fails we don't roll back
        # the move (the MP4 is the real content we want to keep). Log and move on.
        try:
            move_to_recycle_bin(orig)
        except Exception as e:
            recycle_failed += 1
            errors.append({"id": v.id, "reason": "recycle_failed", "error": str(e)})

        try:
            new_size = target.stat().st_size
        except OSError:
            new_size = v.file_size or 0

        v.original_path = str(target)
        v.original_filename = target.name
        v.file_size = new_size
        v.codec = "h264"
        v.convert_status = "skipped"
        v.convert_progress = 0.0
        v.converted_path = None
        replaced += 1

    db.commit()
    return {
        "replaced": replaced,
        "skipped_collision": skipped_collision,
        "move_failed": move_failed,
        "recycle_failed": recycle_failed,
        "errors": errors[:30],
    }


# ---- Screenshot / pack folder cleanup (physical delete) ----

@router.get("/library/screen-folders")
def list_screen_folders(db: Session = Depends(get_db)) -> dict:
    """Scan library roots for screenshot-pack folders (Screens, *_scr, etc.).

    Returns paths, sizes, and file counts sorted biggest first. These folders
    are never read by the app — they're leftover from how the library was
    originally downloaded.
    """
    from pathlib import Path as _P
    from ..models import LibraryFolder
    from ..services.screen_cleanup import find_screenshot_folders

    roots = [
        _P(f.path)
        for f in db.scalars(
            select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
        ).all()
    ]
    return find_screenshot_folders(roots)


@router.post("/library/screen-folders/purge")
def purge_screen_folders(
    paths: list[str] = Body(default=None, embed=True),
    db: Session = Depends(get_db),
) -> dict:
    """Recycle-bin the given screenshot folders. Paths outside any registered
    library root are silently rejected (defense in depth)."""
    from pathlib import Path as _P
    from ..models import LibraryFolder
    from ..services.screen_cleanup import purge_screenshot_folders

    roots = [
        _P(f.path)
        for f in db.scalars(
            select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
        ).all()
    ]
    return purge_screenshot_folders(paths or [], roots)


# ---- Tag normalization (folder-tag cleanup) ----

@router.get("/tags/normalize-preview")
def tags_normalize_preview(db: Session = Depends(get_db)) -> dict:
    """Dry-run: show what tag rename/merge/delete the normalizer would do.

    Read-only — use this to review before calling ``POST .../normalize``.
    """
    from ..services.tag_normalize import plan_tag_normalization
    return plan_tag_normalization(db)


@router.post("/tags/normalize")
def tags_normalize_apply(db: Session = Depends(get_db)) -> dict:
    """Apply the normalization plan: rename tags, merge equivalents,
    delete service-folder tags (screens, incoming, ...).

    All changes run in a single transaction. The scanner already uses
    the same normalizer on write, so subsequent scans stay idempotent.
    """
    from ..services.tag_normalize import apply_tag_normalization
    return apply_tag_normalization(db)


# ---- Encoder info ----

@router.get("/encoder")
def encoder_info() -> dict:
    """Report which video encoder will be used for new jobs."""
    return {
        "effective": get_effective_encoder(),
        "nvenc_available": detect_nvenc_available(),
    }


# ---- Debug ----

@router.get("/debug/video/{video_id}")
def debug_video_metadata(video_id: str, db: Session = Depends(get_db)) -> dict:
    """Compare stored DB metadata against a fresh ffprobe of the source file.

    Useful when a video shows up in the wrong category (e.g. listed as a
    compress candidate even though its dimensions look small) — lets you see
    whether the stored values are stale/wrong vs what ffprobe says today.
    """
    from pathlib import Path
    from ..models import Video
    from ..services.metadata import extract_video_metadata, probe_video

    video = db.get(Video, video_id)
    if not video:
        return {"error": "video_not_found"}

    src = Path(video.original_path) if video.original_path else None
    file_exists = bool(src and src.exists())

    stored = {
        "id": video.id,
        "title": video.title,
        "original_path": video.original_path,
        "width": video.width,
        "height": video.height,
        "duration": video.duration,
        "codec": video.codec,
        "file_size": video.file_size,
        "is_vertical": video.is_vertical,
        "compress_status": video.compress_status,
        "convert_status": video.convert_status,
    }

    fresh: dict | None = None
    raw_streams: list | None = None
    probe_error: str | None = None
    if file_exists and src is not None:
        try:
            fresh = extract_video_metadata(src)
            raw_payload = probe_video(src)
            raw_streams = [
                {
                    "index": s.get("index"),
                    "codec_type": s.get("codec_type"),
                    "codec_name": s.get("codec_name"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                    "disposition": s.get("disposition"),
                    "avg_frame_rate": s.get("avg_frame_rate"),
                }
                for s in raw_payload.get("streams", [])
            ]
        except Exception as exc:
            probe_error = str(exc)

    diverged = bool(
        fresh
        and (
            fresh.get("width") != stored["width"]
            or fresh.get("height") != stored["height"]
            or fresh.get("codec") != stored["codec"]
        )
    )

    return {
        "file_exists": file_exists,
        "stored": stored,
        "fresh_probe": fresh,
        "raw_streams": raw_streams,
        "probe_error": probe_error,
        "stored_vs_fresh_diverged": diverged,
    }


@router.post("/debug/refresh-metadata/{video_id}")
def refresh_video_metadata(video_id: str, db: Session = Depends(get_db)) -> dict:
    """Re-extract metadata for one video and write the fresh values to the DB.

    Use this after fixing a metadata extraction bug to update an individual
    bad row without re-scanning the whole library.
    """
    from pathlib import Path
    from ..models import Video
    from ..services.metadata import extract_video_metadata

    video = db.get(Video, video_id)
    if not video:
        return {"error": "video_not_found"}
    src = Path(video.original_path) if video.original_path else None
    if not src or not src.exists():
        return {"error": "file_missing", "path": video.original_path}

    before = {"width": video.width, "height": video.height, "codec": video.codec}
    try:
        fresh = extract_video_metadata(src)
    except Exception as exc:
        return {"error": "probe_failed", "detail": str(exc)}

    video.width = fresh.get("width", video.width)
    video.height = fresh.get("height", video.height)
    video.codec = fresh.get("codec", video.codec)
    video.is_vertical = fresh.get("is_vertical", video.is_vertical)
    if fresh.get("duration") is not None:
        video.duration = fresh["duration"]
    db.commit()
    return {
        "id": video_id,
        "before": before,
        "after": {"width": video.width, "height": video.height, "codec": video.codec},
        "changed": before != {"width": video.width, "height": video.height, "codec": video.codec},
    }
