from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Video
from ..services.thumbnail import (
    fallback_svg_bytes,
    generate_contact_sheet,
    generate_preview_frame,
    generate_thumbnail,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stream", tags=["streaming"])
CHUNK_SIZE = 1024 * 1024


def guess_media_type(file_path: Path) -> str:
    media_type, _ = mimetypes.guess_type(file_path.name)
    return media_type or "application/octet-stream"


def effective_source_path(video: Video) -> Path | None:
    """Resolve which file should be used for anything derived from the video.

    Prefers the browser-friendly converted MP4 when available — this matters
    not just for playback but also for thumbnails, contact sheets and preview
    frames. Without this, ffmpeg would re-decode the original WMV (slow on
    CPU because NVDEC on Turing doesn't support WMV3/VC-1/MPEG-4 ASP), which
    the user perceives as "transcoding still happening" after conversion.
    """
    if video.convert_status == "completed" and video.converted_path:
        converted = Path(video.converted_path)
        if converted.exists():
            return converted
    if video.original_path:
        original = Path(video.original_path)
        if original.exists():
            return original
    return None


@router.get("/{video_id}/hls/{path:path}", name="stream_hls")
def stream_hls(video_id: str, path: str, db: Session = Depends(get_db)):
    """Serve HLS master playlist, variant playlists, and .ts segments."""
    from ..config import get_settings
    settings = get_settings()
    hls_dir = settings.hls_dir / video_id
    file_path = hls_dir / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="HLS file not found")

    if path.endswith(".m3u8"):
        return FileResponse(file_path, media_type="application/vnd.apple.mpegurl")
    elif path.endswith(".ts"):
        return FileResponse(file_path, media_type="video/mp2t")
    else:
        return FileResponse(file_path)


@router.get("/{video_id}/raw", name="stream_raw_video")
def stream_raw_video(
    request: Request,
    video_id: str,
    db: Session = Depends(get_db),
):
    video = db.get(Video, video_id)
    if video is None:
        logger.warning("stream_raw: video not found id=%s", video_id)
        raise HTTPException(status_code=404, detail="Video not found")

    file_path = effective_source_path(video)
    if file_path is None:
        logger.error("stream_raw: FILE MISSING id=%s original=%s converted=%s",
                     video_id, video.original_path, video.converted_path)
        raise HTTPException(status_code=404, detail="Video file is missing")
    logger.info("stream_raw: id=%s path=%s", video_id, file_path)

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")
    media_type = guess_media_type(file_path)
    logger.info("stream_raw: size=%d range=%s", file_size, range_header)

    if not range_header:
        logger.info("stream_raw: returning full FileResponse for %s", file_path.name)
        return FileResponse(
            file_path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"},
        )

    start, end = parse_range_header(range_header, file_size)
    logger.info("stream_raw: range %d-%d / %d", start, end, file_size)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(
        iter_file_range(file_path, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=media_type,
        headers=headers,
    )


@router.get("/{video_id}/thumbnail", name="stream_video_thumbnail")
def stream_video_thumbnail(video_id: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    file_path = effective_source_path(video)
    if file_path is None:
        logger.warning("thumbnail: source file missing id=%s", video_id)
        return Response(content=fallback_svg_bytes(video.title), media_type="image/svg+xml")

    try:
        thumbnail = generate_thumbnail(file_path, video.id, video.duration)
        return FileResponse(thumbnail, media_type="image/jpeg", filename=thumbnail.name)
    except (FileNotFoundError, RuntimeError):
        return Response(content=fallback_svg_bytes(video.title), media_type="image/svg+xml")


@router.get("/{video_id}/contact-sheet", name="stream_contact_sheet")
def stream_contact_sheet(video_id: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    file_path = effective_source_path(video)
    if file_path is None:
        logger.warning("contact-sheet: source file missing id=%s", video_id)
        return Response(content=fallback_svg_bytes(video.title), media_type="image/svg+xml")
    try:
        sheet = generate_contact_sheet(file_path, video.id, video.duration)
        return FileResponse(sheet, media_type="image/jpeg", filename=sheet.name)
    except (FileNotFoundError, RuntimeError):
        return Response(content=fallback_svg_bytes(video.title), media_type="image/svg+xml")


@router.get("/{video_id}/preview-frame/{frame_index}", name="stream_video_preview_frame")
def stream_video_preview_frame(
    video_id: str,
    frame_index: int,
    db: Session = Depends(get_db),
):
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    file_path = effective_source_path(video)
    if file_path is None:
        logger.warning("preview-frame: source file missing id=%s", video_id)
        return Response(content=fallback_svg_bytes(f"{video.title}-{frame_index}"), media_type="image/svg+xml")

    try:
        frame = generate_preview_frame(file_path, video.id, frame_index, video.duration)
        return FileResponse(frame, media_type="image/jpeg", filename=frame.name)
    except (FileNotFoundError, RuntimeError):
        return Response(content=fallback_svg_bytes(f"{video.title}-{frame_index}"), media_type="image/svg+xml")


def parse_range_header(range_header: str, file_size: int) -> tuple[int, int]:
    try:
        units, values = range_header.split("=", maxsplit=1)
        if units.strip().lower() != "bytes":
            raise ValueError
        start_raw, end_raw = values.split("-", maxsplit=1)

        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
        else:
            suffix_length = int(end_raw)
            if suffix_length <= 0:
                raise ValueError
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
    except ValueError as error:
        raise HTTPException(status_code=416, detail="Invalid Range header") from error

    if end >= file_size:
        end = file_size - 1

    if start < 0 or start >= file_size or start > end:
        raise HTTPException(status_code=416, detail="Range not satisfiable")
    return start, end


def iter_file_range(file_path: Path, start: int, end: int):
    with file_path.open("rb") as stream:
        stream.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk_size = min(CHUNK_SIZE, remaining)
            data = stream.read(chunk_size)
            if not data:
                break
            remaining -= len(data)
            yield data
