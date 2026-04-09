from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ScanResult
from ..services.scanner import get_scan_progress, scan_library
from ..services.transcoder import enqueue_all_pending, enqueue_video, get_queue_status, start_worker


router = APIRouter(prefix="/transcode", tags=["library"])


@router.post("/scan", response_model=ScanResult)
def scan_videos(db: Session = Depends(get_db)) -> ScanResult:
    result = scan_library(db)
    return ScanResult(**result)


@router.get("/scan/progress")
def scan_progress() -> dict:
    """Poll this endpoint to get live scan progress."""
    return get_scan_progress()


@router.post("/start/{video_id}")
def transcode_one(video_id: str) -> dict:
    enqueue_video(video_id)
    start_worker()
    return {"status": "queued", "video_id": video_id}


@router.post("/start-all")
def transcode_all_pending(db: Session = Depends(get_db)) -> dict:
    count = enqueue_all_pending(db)
    start_worker()
    return {"status": "queued", "count": count}


@router.get("/queue")
def queue_status() -> dict:
    return get_queue_status()
