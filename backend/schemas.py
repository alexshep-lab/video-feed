from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VideoBase(BaseModel):
    id: str
    title: str
    description: str | None
    original_filename: str
    duration: float | None
    width: int | None
    height: int | None
    file_size: int
    codec: str | None
    transcode_status: str
    transcode_progress: float
    thumbnail_path: str | None
    library_path: str | None
    category: str | None
    is_vertical: bool
    favorite: bool = False
    confirmed: bool = False
    view_count: int = 0
    total_watch_time: float = 0.0
    last_watched_at: datetime | None = None
    deleted_at: datetime | None = None
    compress_status: str = "none"
    compress_progress: float = 0.0
    compressed_size: int | None = None
    convert_status: str = "none"
    convert_progress: float = 0.0
    added_at: datetime
    tag_list: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class VideoListItem(VideoBase):
    original_path: str
    raw_stream_url: str
    hls_stream_url: str | None = None
    thumbnail_url: str
    preview_frame_template_url: str


class VideoDetail(VideoBase):
    original_path: str
    raw_stream_url: str
    hls_path: str | None
    hls_stream_url: str | None = None
    thumbnail_url: str
    preview_frame_template_url: str


class VideoUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    category: str | None = None
    favorite: bool | None = None
    confirmed: bool | None = None
    tag_list: list[str] | None = None


class ScanResult(BaseModel):
    scanned_files: int
    created: int
    updated: int
    unchanged: int = 0
    skipped: int
    ffprobe_available: bool


# --- Tags ---

class TagOut(BaseModel):
    id: int
    name: str
    video_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class TagCreate(BaseModel):
    name: str


# --- Library Folders ---

class LibraryFolderOut(BaseModel):
    id: int
    path: str
    enabled: bool
    is_incoming: bool = False
    display_name: str | None
    video_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class LibraryFolderUpdate(BaseModel):
    enabled: bool | None = None
    is_incoming: bool | None = None
    display_name: str | None = None


class LibraryFolderCreate(BaseModel):
    path: str
    display_name: str | None = None


# --- Filters ---

class FilterOptions(BaseModel):
    """Available filter values for the UI to populate dropdowns."""
    categories: list[str]
    codecs: list[str]
    libraries: list[LibraryFolderOut]
    tags: list[TagOut]
    duration_ranges: list[dict] = Field(default_factory=lambda: [
        {"key": "short", "label": "< 1 min", "max": 60},
        {"key": "medium", "label": "1-5 min", "min": 60, "max": 300},
        {"key": "long", "label": "5-30 min", "min": 300, "max": 1800},
        {"key": "movie", "label": "> 30 min", "min": 1800},
    ])
