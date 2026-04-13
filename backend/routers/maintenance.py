"""Maintenance endpoints: duplicates, compression, contact sheets, etc."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.compressor import (
    build_compressed_output_path,
    count_oversized_candidates,
    enqueue_compress,
    enqueue_oversized,
    get_oversized_candidates,
    get_compress_status,
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
    count_videos_without_palette,
    enqueue_missing_palettes,
    enqueue_one as enqueue_palette_one,
    get_palette_status,
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
        if not needs_conversion(video.original_path):
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
    if not needs_conversion(video.original_path):
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


@router.post("/palettes/generate-all")
def palette_generate_all(db: Session = Depends(get_db)) -> dict:
    count = enqueue_missing_palettes(db)
    start_palette_worker()
    return {"status": "queued", "count": count}


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
