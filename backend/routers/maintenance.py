"""Maintenance endpoints: duplicates, compression, contact sheets, etc."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
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
)
from ..services.duplicates import (
    compute_all_phashes,
    find_phash_duplicates,
    find_size_duration_duplicates,
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
