from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import Select, desc, func, or_, select
from sqlalchemy.sql.expression import text
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LibraryFolder, Tag, Video, video_tags
from ..schemas import (
    FilterOptions,
    LibraryFolderOut,
    TagOut,
    VideoDetail,
    VideoListItem,
    VideoUpdate,
)
from ..services.converter import NEEDS_CONVERSION_EXTENSIONS
from ..services.palette import palette_exists


router = APIRouter(prefix="/videos", tags=["videos"])


def _apply_ready_sql(statement):
    """Restrict to videos whose source is (or will be) playable in a browser.

    SQL-level check — catches the common "needs conversion and conversion is
    done" case. The per-video palette-existence check is layered on top in
    Python because it's a filesystem lookup.
    """
    from sqlalchemy import or_, and_, not_
    ext_clauses = [Video.original_path.ilike(f"%{ext}") for ext in NEEDS_CONVERSION_EXTENSIONS]
    needs_conv = or_(*ext_clauses)
    # A row is playable if:
    #   - the original doesn't need conversion (native mp4/webm/mkv/mov/etc.), OR
    #   - conversion finished successfully
    return statement.where(
        or_(not_(needs_conv), Video.convert_status == "completed")
    )


def _video_is_review_ready(video: Video) -> bool:
    """Second-stage Python filter for ``ready=true``: palette file on disk."""
    return palette_exists(video.id)


# ---- List / Search / Filter ----

@router.get("", response_model=list[VideoListItem])
def list_videos(
    request: Request,
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    category: str | None = Query(default=None),
    library: str | None = Query(default=None),
    codec: str | None = Query(default=None),
    duration_min: float | None = Query(default=None),
    duration_max: float | None = Query(default=None),
    is_vertical: bool | None = Query(default=None),
    favorite: bool | None = Query(default=None),
    confirmed: bool | None = Query(default=None),
    ready: bool | None = Query(default=None),
    show_deleted: bool = Query(default=False),
    sort: str = Query(default="shuffle"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[VideoListItem]:
    ordering = {
        "newest": desc(Video.added_at),
        "oldest": Video.added_at,
        "title": Video.title,
        "duration": Video.duration,
        "size": desc(Video.file_size),
        "most_viewed": desc(Video.view_count),
        "last_watched": desc(Video.last_watched_at),
    }
    if sort == "shuffle":
        statement: Select[tuple[Video]] = select(Video).order_by(text("RANDOM()"))
    else:
        statement = select(Video).order_by(ordering.get(sort, desc(Video.added_at)))

    # Only show videos from enabled library folders
    enabled_folders = db.scalars(
        select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
    ).all()
    if enabled_folders:
        enabled_paths = [f.path for f in enabled_folders]
        statement = statement.where(Video.library_path.in_(enabled_paths))

    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(Video.title.ilike(pattern), Video.original_filename.ilike(pattern))
        )
    if tag:
        statement = statement.join(video_tags).join(Tag).where(Tag.name == tag)
    if category:
        statement = statement.where(Video.category == category)
    if library:
        statement = statement.where(Video.library_path == library)
    if codec:
        statement = statement.where(Video.codec == codec)
    if duration_min is not None:
        statement = statement.where(Video.duration >= duration_min)
    if duration_max is not None:
        statement = statement.where(Video.duration <= duration_max)
    if is_vertical is not None:
        statement = statement.where(Video.is_vertical == is_vertical)
    if favorite is not None:
        statement = statement.where(Video.favorite == favorite)
    if confirmed is not None:
        statement = statement.where(Video.confirmed == confirmed)
    if not show_deleted:
        statement = statement.where(Video.deleted_at.is_(None))
    if ready:
        statement = _apply_ready_sql(statement)

    if not ready:
        videos = db.scalars(statement.offset(offset).limit(limit)).all()
    else:
        # When ready=true we need to additionally check palette existence on
        # disk. Fetch a window larger than `limit`, filter, then slice. This
        # keeps the DB query simple at the cost of possibly over-fetching.
        FETCH_MULTIPLIER = 4
        fetch_limit = limit * FETCH_MULTIPLIER
        all_rows = db.scalars(statement.offset(offset).limit(fetch_limit)).all()
        videos = [v for v in all_rows if _video_is_review_ready(v)][:limit]
    return [to_list_item(request, video) for video in videos]


@router.get("/count")
def count_videos(
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    category: str | None = Query(default=None),
    library: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(func.count(Video.id))

    enabled_folders = db.scalars(
        select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
    ).all()
    if enabled_folders:
        enabled_paths = [f.path for f in enabled_folders]
        statement = statement.where(Video.library_path.in_(enabled_paths))

    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(Video.title.ilike(pattern), Video.original_filename.ilike(pattern))
        )
    if tag:
        statement = statement.select_from(Video).join(video_tags).join(Tag).where(Tag.name == tag)
    if category:
        statement = statement.where(Video.category == category)
    if library:
        statement = statement.where(Video.library_path == library)

    total = db.scalar(statement) or 0
    return {"total": total}


# ---- Next in sequence (for review auto-advance) ----

@router.get("/next")
def next_video(
    request: Request,
    after: str | None = Query(default=None, description="ID of the current video — returned next will come after it"),
    q: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    category: str | None = Query(default=None),
    library: str | None = Query(default=None),
    codec: str | None = Query(default=None),
    duration_min: float | None = Query(default=None),
    duration_max: float | None = Query(default=None),
    is_vertical: bool | None = Query(default=None),
    favorite: bool | None = Query(default=None),
    confirmed: bool | None = Query(default=None),
    ready: bool | None = Query(default=None),
    sort: str = Query(default="newest"),
    db: Session = Depends(get_db),
) -> dict:
    """Return the ID of the next video matching the same filters as ``GET /videos``.

    Used by the review-mode auto-advance on the watch page: after confirming
    or hard-deleting a video, the frontend calls this to figure out what to
    navigate to next. ``sort=shuffle`` is intentionally not exposed here —
    for a deterministic "next" you want a stable ordering.
    """
    # Build the same query we use for listing, without a pre-filter on `after`.
    ordering = {
        "newest": desc(Video.added_at),
        "oldest": Video.added_at,
        "title": Video.title,
        "duration": Video.duration,
        "size": desc(Video.file_size),
        "most_viewed": desc(Video.view_count),
        "last_watched": desc(Video.last_watched_at),
    }
    order_by = ordering.get(sort, desc(Video.added_at))

    statement: Select[tuple[Video]] = select(Video).order_by(order_by)

    enabled_folders = db.scalars(
        select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
    ).all()
    if enabled_folders:
        enabled_paths = [f.path for f in enabled_folders]
        statement = statement.where(Video.library_path.in_(enabled_paths))

    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(Video.title.ilike(pattern), Video.original_filename.ilike(pattern))
        )
    if tag:
        statement = statement.join(video_tags).join(Tag).where(Tag.name == tag)
    if category:
        statement = statement.where(Video.category == category)
    if library:
        statement = statement.where(Video.library_path == library)
    if codec:
        statement = statement.where(Video.codec == codec)
    if duration_min is not None:
        statement = statement.where(Video.duration >= duration_min)
    if duration_max is not None:
        statement = statement.where(Video.duration <= duration_max)
    if is_vertical is not None:
        statement = statement.where(Video.is_vertical == is_vertical)
    if favorite is not None:
        statement = statement.where(Video.favorite == favorite)
    if confirmed is not None:
        statement = statement.where(Video.confirmed == confirmed)
    statement = statement.where(Video.deleted_at.is_(None))
    if ready:
        statement = _apply_ready_sql(statement)

    # Iterate through the query results, skip until we pass `after`, apply the
    # ready-palette check on the Python side, and return the first matching id.
    found_after = after is None
    # Limit cap to avoid loading everything for huge libraries
    for video in db.scalars(statement.limit(5000)):
        if not found_after:
            if video.id == after:
                found_after = True
            continue
        if ready and not _video_is_review_ready(video):
            continue
        return {"next_id": video.id, "next": to_list_item(request, video).model_dump()}
    return {"next_id": None, "next": None}


# ---- Filters metadata ----

@router.get("/filters", response_model=FilterOptions)
def get_filter_options(db: Session = Depends(get_db)) -> FilterOptions:
    # Categories
    cats = db.execute(
        select(Video.category, func.count(Video.id))
        .where(Video.category.isnot(None), Video.category != "")
        .group_by(Video.category)
        .order_by(func.count(Video.id).desc())
    ).all()
    categories = [row[0] for row in cats]

    # Codecs
    codec_rows = db.execute(
        select(Video.codec, func.count(Video.id))
        .where(Video.codec.isnot(None), Video.codec != "")
        .group_by(Video.codec)
        .order_by(func.count(Video.id).desc())
    ).all()
    codecs = [row[0] for row in codec_rows]

    # Tags with counts
    tag_rows = db.execute(
        select(Tag.id, Tag.name, func.count(video_tags.c.video_id))
        .outerjoin(video_tags, Tag.id == video_tags.c.tag_id)
        .group_by(Tag.id, Tag.name)
        .order_by(Tag.name)
    ).all()
    tags = [TagOut(id=row[0], name=row[1], video_count=row[2]) for row in tag_rows]

    # Library folders with counts
    folder_rows = db.execute(
        select(LibraryFolder, func.count(Video.id))
        .outerjoin(Video, Video.library_path == LibraryFolder.path)
        .group_by(LibraryFolder.id)
        .order_by(LibraryFolder.path)
    ).all()
    libraries = [
        LibraryFolderOut(
            id=row[0].id,
            path=row[0].path,
            enabled=row[0].enabled,
            is_incoming=row[0].is_incoming,
            display_name=row[0].display_name,
            video_count=row[1],
        )
        for row in folder_rows
    ]

    return FilterOptions(categories=categories, codecs=codecs, tags=tags, libraries=libraries)


# ---- Random ----

@router.get("/random", response_model=VideoListItem)
def get_random_video(request: Request, db: Session = Depends(get_db)) -> VideoListItem:
    video = db.scalar(
        select(Video)
        .where(Video.deleted_at.is_(None))
        .order_by(text("RANDOM()"))
        .limit(1)
    )
    if not video:
        raise HTTPException(status_code=404, detail="No videos available")
    return to_list_item(request, video)


# ---- Single Video ----

@router.get("/{video_id}", response_model=VideoDetail)
def get_video(video_id: str, request: Request, db: Session = Depends(get_db)) -> VideoDetail:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return to_detail_item(request, video)


@router.get("/{video_id}/recommendations", response_model=list[VideoListItem])
def get_recommendations(
    video_id: str,
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> list[VideoListItem]:
    """Simple scoring: shared tags * 3 + same library * 2 + similar duration * 1."""
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    tag_ids = [t.id for t in video.tag_objects]
    duration = video.duration or 0

    # Candidates: videos sharing at least one tag, OR same library
    statement = select(Video).where(Video.id != video_id, Video.deleted_at.is_(None))
    if tag_ids:
        statement = statement.where(
            Video.id.in_(
                select(video_tags.c.video_id).where(video_tags.c.tag_id.in_(tag_ids))
            )
        )
    elif video.library_path:
        statement = statement.where(Video.library_path == video.library_path)

    candidates = db.scalars(statement.limit(200)).all()

    def score(v: Video) -> int:
        s = 0
        v_tag_ids = {t.id for t in v.tag_objects}
        s += len(v_tag_ids & set(tag_ids)) * 3
        if v.library_path == video.library_path:
            s += 2
        if duration and v.duration:
            ratio = min(duration, v.duration) / max(duration, v.duration)
            if ratio > 0.7:
                s += 1
        return s

    ranked = sorted(candidates, key=score, reverse=True)[:limit]
    return [to_list_item(request, v) for v in ranked]


# ---- Delete / Restore ----

@router.delete("/{video_id}")
def delete_video(
    video_id: str,
    hard: bool = Query(default=False),
    recycle: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    """Soft delete by default. recycle=true moves file to OS recycle bin. hard=true deletes permanently."""
    from datetime import datetime, timezone
    from pathlib import Path
    import os
    from ..services.fileops import move_to_recycle_bin

    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    if hard and recycle:
        raise HTTPException(status_code=400, detail="Use either hard delete or recycle, not both")

    if recycle:
        try:
            p = Path(video.original_path)
            if p.exists():
                move_to_recycle_bin(p)
        except Exception as e:
            raise HTTPException(500, f"Failed to move file to Recycle Bin: {e}")
        db.delete(video)
        db.commit()
        return {"status": "deleted_recycle"}

    if hard:
        try:
            p = Path(video.original_path)
            if p.exists():
                os.remove(p)
        except Exception as e:
            raise HTTPException(500, f"Failed to delete file: {e}")
        db.delete(video)
        db.commit()
        return {"status": "deleted_hard"}

    video.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "deleted_soft"}


@router.post("/{video_id}/move")
def move_video(
    video_id: str,
    folder_id: int = Query(...),
    confirm: bool = Query(default=False),
    additional_tags: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Move video file to another library folder and reapply auto-tags.

    additional_tags: comma-separated list of extra tags to add.
    confirm=true marks the video as confirmed.
    """
    import shutil
    from pathlib import Path
    from ..services.scanner import _apply_folder_tags

    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    target_folder = db.get(LibraryFolder, folder_id)
    if target_folder is None:
        raise HTTPException(status_code=404, detail="Target folder not found")

    src = Path(video.original_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail="Source file missing")

    target_dir = Path(target_folder.path)
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / src.name

    # Avoid overwriting
    counter = 1
    while dst.exists():
        stem = src.stem
        suffix = src.suffix
        dst = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    try:
        shutil.move(str(src), str(dst))
    except Exception as e:
        raise HTTPException(500, f"Failed to move file: {e}")

    # Update video record
    video.original_path = str(dst.resolve())
    video.original_filename = dst.name
    video.library_path = str(target_dir.resolve())
    if confirm:
        video.confirmed = True

    # Reapply folder-based tags
    all_libs = db.scalars(select(LibraryFolder).where(LibraryFolder.enabled == True)).all()  # noqa: E712
    lib_dirs = [Path(f.path) for f in all_libs]
    _apply_folder_tags(db, video, dst, target_dir, {}, lib_dirs)

    # Add additional manual tags
    if additional_tags:
        extra = [t.strip().lower() for t in additional_tags.split(",") if t.strip()]
        existing = {t.name for t in video.tag_objects}
        for name in extra:
            if name in existing:
                continue
            tag = db.scalar(select(Tag).where(Tag.name == name))
            if not tag:
                tag = Tag(name=name)
                db.add(tag)
                db.flush()
            video.tag_objects.append(tag)

    db.commit()
    return {"status": "ok", "new_path": str(dst), "library_path": video.library_path}


@router.post("/{video_id}/restore")
def restore_video(video_id: str, db: Session = Depends(get_db)) -> dict:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    video.deleted_at = None
    db.commit()
    return {"status": "restored"}


@router.post("/bulk-action")
def bulk_action(
    action: str = Query(...),
    video_ids: list[str] = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    """Bulk operations: confirm, unconfirm, delete-soft, delete-hard, favorite, unfavorite, restore."""
    from datetime import datetime, timezone
    from pathlib import Path
    import os

    affected = 0
    for vid in video_ids:
        video = db.get(Video, vid)
        if not video:
            continue
        if action == "confirm":
            video.confirmed = True
        elif action == "unconfirm":
            video.confirmed = False
        elif action == "favorite":
            video.favorite = True
        elif action == "unfavorite":
            video.favorite = False
        elif action == "delete-soft":
            video.deleted_at = datetime.now(timezone.utc)
        elif action == "restore":
            video.deleted_at = None
        elif action == "delete-hard":
            try:
                p = Path(video.original_path)
                if p.exists():
                    os.remove(p)
            except Exception:
                pass
            db.delete(video)
        affected += 1
    db.commit()
    return {"status": "ok", "affected": affected}


@router.patch("/{video_id}", response_model=VideoDetail)
def update_video(
    video_id: str,
    payload: VideoUpdate,
    request: Request,
    db: Session = Depends(get_db),
) -> VideoDetail:
    video = db.get(Video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    if payload.title is not None:
        video.title = payload.title
    if payload.description is not None:
        video.description = payload.description
    if payload.category is not None:
        video.category = payload.category
    if payload.favorite is not None:
        video.favorite = payload.favorite
    if payload.confirmed is not None:
        video.confirmed = payload.confirmed
    if payload.tag_list is not None:
        _sync_tags(db, video, payload.tag_list)

    db.commit()
    db.refresh(video)
    return to_detail_item(request, video)


# ---- Helpers ----

def _sync_tags(db: Session, video: Video, tag_names: list[str]) -> None:
    """Set video's tags to exactly this list, creating new Tag records as needed."""
    cleaned = sorted({name.strip().lower() for name in tag_names if name.strip()})
    existing_tags = db.scalars(select(Tag).where(Tag.name.in_(cleaned))).all()
    existing_map = {t.name: t for t in existing_tags}

    result = []
    for name in cleaned:
        if name in existing_map:
            result.append(existing_map[name])
        else:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
            result.append(tag)

    video.tag_objects = result


def to_list_item(request: Request, video: Video) -> VideoListItem:
    return VideoListItem(**to_detail_item(request, video).model_dump())


def to_detail_item(request: Request, video: Video) -> VideoDetail:
    hls_url = None
    if video.transcode_status == "completed" and video.hls_path:
        hls_url = str(request.url_for("stream_hls", video_id=video.id, path="master.m3u8"))

    return VideoDetail(
        id=video.id,
        title=video.title,
        description=video.description,
        original_filename=video.original_filename,
        duration=video.duration,
        width=video.width,
        height=video.height,
        file_size=video.file_size,
        codec=video.codec,
        transcode_status=video.transcode_status,
        transcode_progress=video.transcode_progress,
        thumbnail_path=video.thumbnail_path,
        library_path=video.library_path,
        category=video.category,
        is_vertical=video.is_vertical,
        favorite=video.favorite,
        confirmed=video.confirmed,
        view_count=video.view_count,
        total_watch_time=video.total_watch_time,
        last_watched_at=video.last_watched_at,
        deleted_at=video.deleted_at,
        compress_status=video.compress_status or "none",
        compress_progress=video.compress_progress or 0.0,
        compressed_size=video.compressed_size,
        convert_status=video.convert_status or "none",
        convert_progress=video.convert_progress or 0.0,
        added_at=video.added_at,
        original_path=video.original_path,
        raw_stream_url=str(request.url_for("stream_raw_video", video_id=video.id)),
        hls_path=video.hls_path,
        hls_stream_url=hls_url,
        tags=video.tags,
        tag_list=video.tag_list,
        thumbnail_url=str(request.url_for("stream_video_thumbnail", video_id=video.id)),
        preview_frame_template_url=str(
            request.url_for("stream_video_preview_frame", video_id=video.id, frame_index=0)
        ).removesuffix("/0"),
    )
