from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# Many-to-many association table for Video <-> Tag
video_tags = Table(
    "video_tags",
    Base.metadata,
    Column("video_id", String(36), ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    videos: Mapped[list[Video]] = relationship("Video", secondary=video_tags, back_populates="tag_objects")

    def __repr__(self) -> str:
        return f"<Tag {self.name!r}>"


class LibraryFolder(Base):
    __tablename__ = "library_folders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_incoming: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    def __repr__(self) -> str:
        return f"<LibraryFolder {self.path!r} enabled={self.enabled}>"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(512), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(String(1024), index=True)
    original_path: Mapped[str] = mapped_column(String(2048), unique=True)
    library_path: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer)
    file_mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transcode_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    transcode_progress: Mapped[float] = mapped_column(Float, default=0.0)
    hls_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tags: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    is_vertical: Mapped[bool] = mapped_column(Boolean, default=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    total_watch_time: Mapped[float] = mapped_column(Float, default=0.0)
    last_watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Duplicate detection
    phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Compression
    compress_status: Mapped[str] = mapped_column(String(32), default="none", index=True)  # none|pending|processing|completed|failed
    compress_progress: Mapped[float] = mapped_column(Float, default=0.0)
    compressed_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    compressed_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Browser-friendly conversion (e.g. WMV -> MP4). Original file is kept until validated.
    convert_status: Mapped[str] = mapped_column(String(32), default="none", index=True)  # none|pending|processing|completed|failed|skipped
    convert_progress: Mapped[float] = mapped_column(Float, default=0.0)
    converted_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    tag_objects: Mapped[list[Tag]] = relationship("Tag", secondary=video_tags, back_populates="videos", lazy="selectin")

    @property
    def tag_list(self) -> list[str]:
        return [t.name for t in self.tag_objects]


class WatchProgress(Base):
    __tablename__ = "watch_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[float] = mapped_column(Float, default=0.0)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class WatchEvent(Base):
    """Log of each time a video is opened/played. Used for stats and history."""
    __tablename__ = "watch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(36), ForeignKey("videos.id"), index=True)
    watched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    watch_duration: Mapped[float] = mapped_column(Float, default=0.0)  # seconds actually watched

    video: Mapped[Video] = relationship("Video", lazy="joined")
