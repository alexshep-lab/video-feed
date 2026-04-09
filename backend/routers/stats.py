from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Tag, Video, WatchEvent, video_tags


router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("")
def get_stats(db: Session = Depends(get_db)) -> dict:
    """Aggregated statistics for the stats page."""
    # Total videos and library size
    total_videos = db.scalar(select(func.count(Video.id))) or 0
    total_size = db.scalar(select(func.sum(Video.file_size))) or 0
    total_duration = db.scalar(select(func.sum(Video.duration))) or 0
    total_favorites = db.scalar(select(func.count(Video.id)).where(Video.favorite == True)) or 0  # noqa: E712

    # Watch stats
    total_views = db.scalar(select(func.sum(Video.view_count))) or 0
    total_watch_time = db.scalar(select(func.sum(Video.total_watch_time))) or 0

    # Most viewed videos (top 10)
    most_viewed = db.execute(
        select(Video.id, Video.title, Video.view_count, Video.total_watch_time, Video.duration)
        .where(Video.view_count > 0)
        .order_by(desc(Video.view_count))
        .limit(10)
    ).all()

    # Most watched by time (top 10)
    most_watched_time = db.execute(
        select(Video.id, Video.title, Video.total_watch_time, Video.view_count, Video.duration)
        .where(Video.total_watch_time > 0)
        .order_by(desc(Video.total_watch_time))
        .limit(10)
    ).all()

    # Recent watch history (last 30)
    recent_events = db.execute(
        select(WatchEvent.video_id, WatchEvent.watched_at, WatchEvent.watch_duration, Video.title)
        .join(Video, Video.id == WatchEvent.video_id)
        .order_by(desc(WatchEvent.watched_at))
        .limit(30)
    ).all()

    # Popular tags by total views
    tag_stats = db.execute(
        select(Tag.name, func.sum(Video.view_count), func.count(Video.id))
        .join(video_tags, Tag.id == video_tags.c.tag_id)
        .join(Video, Video.id == video_tags.c.video_id)
        .group_by(Tag.name)
        .having(func.sum(Video.view_count) > 0)
        .order_by(desc(func.sum(Video.view_count)))
        .limit(20)
    ).all()

    # Favorites
    favorites = db.execute(
        select(Video.id, Video.title, Video.view_count, Video.total_watch_time, Video.duration)
        .where(Video.favorite == True)  # noqa: E712
        .order_by(desc(Video.view_count))
        .limit(20)
    ).all()

    # Watch activity by day (last 30 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    daily_activity = db.execute(
        select(
            func.date(WatchEvent.watched_at),
            func.count(WatchEvent.id),
            func.sum(WatchEvent.watch_duration),
        )
        .where(WatchEvent.watched_at >= cutoff)
        .group_by(func.date(WatchEvent.watched_at))
        .order_by(func.date(WatchEvent.watched_at))
    ).all()

    return {
        "overview": {
            "total_videos": total_videos,
            "total_size_bytes": total_size,
            "total_duration_seconds": total_duration,
            "total_favorites": total_favorites,
            "total_views": total_views,
            "total_watch_time_seconds": total_watch_time,
        },
        "most_viewed": [
            {"id": r[0], "title": r[1], "view_count": r[2], "total_watch_time": r[3], "duration": r[4]}
            for r in most_viewed
        ],
        "most_watched_time": [
            {"id": r[0], "title": r[1], "total_watch_time": r[2], "view_count": r[3], "duration": r[4]}
            for r in most_watched_time
        ],
        "recent_history": [
            {"video_id": r[0], "watched_at": r[1].isoformat() if r[1] else None, "watch_duration": r[2], "title": r[3]}
            for r in recent_events
        ],
        "popular_tags": [
            {"name": r[0], "total_views": r[1], "video_count": r[2]}
            for r in tag_stats
        ],
        "favorites": [
            {"id": r[0], "title": r[1], "view_count": r[2], "total_watch_time": r[3], "duration": r[4]}
            for r in favorites
        ],
        "daily_activity": [
            {"date": str(r[0]), "views": r[1], "watch_time": r[2] or 0}
            for r in daily_activity
        ],
    }


@router.post("/watch-event")
def record_watch_event(
    video_id: str = Query(...),
    duration: float = Query(default=0),
    db: Session = Depends(get_db),
) -> dict:
    """Record a watch event. Called by the frontend when video is played."""
    video = db.get(Video, video_id)
    if not video:
        return {"status": "not_found"}

    # Update video stats
    video.view_count = (video.view_count or 0) + 1
    video.total_watch_time = (video.total_watch_time or 0) + duration
    video.last_watched_at = datetime.now(timezone.utc)

    # Create event log
    event = WatchEvent(video_id=video_id, watch_duration=duration)
    db.add(event)
    db.commit()
    return {"status": "ok", "view_count": video.view_count}


@router.post("/update-watch-time")
def update_watch_time(
    video_id: str = Query(...),
    seconds: float = Query(default=0),
    db: Session = Depends(get_db),
) -> dict:
    """Increment watch time for a video. Called periodically during playback."""
    video = db.get(Video, video_id)
    if not video:
        return {"status": "not_found"}
    video.total_watch_time = (video.total_watch_time or 0) + seconds
    video.last_watched_at = datetime.now(timezone.utc)

    # Also update the latest watch event
    latest = db.scalar(
        select(WatchEvent)
        .where(WatchEvent.video_id == video_id)
        .order_by(desc(WatchEvent.watched_at))
        .limit(1)
    )
    if latest:
        latest.watch_duration = (latest.watch_duration or 0) + seconds

    db.commit()
    return {"status": "ok"}
