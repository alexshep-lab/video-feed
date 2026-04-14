from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LibraryFolder, Tag, Video
from .converter import enqueue_convert, needs_conversion, start_convert_worker
from .metadata import SUPPORTED_EXTENSIONS, extract_video_metadata
from .thumbnail import generate_thumbnail

# Shared scan progress state (thread-safe reads)
_scan_progress = {
    "running": False,
    "total_files": 0,
    "processed": 0,
    "created": 0,
    "phase": "",  # "counting" | "scanning" | "done"
}
_lock = threading.Lock()

BATCH_SIZE = 20  # Commit every N files to avoid long DB locks


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.encode("utf-8", errors="replace").decode("utf-8")


def build_move_lookup(videos: list[Video]) -> dict[tuple[str, int], list[Video]]:
    lookup: dict[tuple[str, int], list[Video]] = {}
    for video in videos:
        key = (sanitize_text(video.original_filename) or "", video.file_size or 0)
        lookup.setdefault(key, []).append(video)
    return lookup


def find_moved_video_candidate(
    lookup: dict[tuple[str, int], list[Video]],
    filename: str,
    file_size: int,
    duration: float | None,
) -> Video | None:
    candidates = lookup.get((sanitize_text(filename) or "", file_size), [])
    if not candidates:
        return None

    for video in list(candidates):
        old_path = Path(video.original_path)
        if old_path.exists():
            continue
        if duration is None or video.duration is None or abs(video.duration - duration) <= 2.0:
            candidates.remove(video)
            return video
    return None


def get_scan_progress() -> dict:
    with _lock:
        return dict(_scan_progress)


def _update_progress(**kwargs) -> None:
    with _lock:
        _scan_progress.update(kwargs)


def scan_library(session: Session, force_metadata: bool = False) -> dict:
    """Scan all enabled library directories and index video files."""
    _update_progress(running=True, total_files=0, processed=0, created=0, phase="counting")

    created = 0
    updated = 0
    unchanged = 0
    ffprobe_is_available = True
    scanned_files = 0
    batch_count = 0

    # Get enabled library folders from DB
    folders = session.scalars(
        select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
    ).all()

    if not folders:
        _update_progress(running=False, phase="done")
        return {
            "scanned_files": 0, "created": 0, "updated": 0,
            "unchanged": 0, "skipped": 0, "ffprobe_available": True,
        }

    library_dirs = [Path(f.path) for f in folders]

    # Phase 1: Count total files for progress.
    # Filter by extension BEFORE is_file() — rglob("*") yields every directory
    # and non-video file too (tens of thousands on a big library), and is_file()
    # is a syscall per entry. Suffix check is a cheap string op, so we prune
    # the obvious non-matches before touching the disk.
    total = 0
    all_files: list[tuple[Path, Path]] = []  # (file_path, library_dir)
    for library_dir in library_dirs:
        if not library_dir.exists() or not library_dir.is_dir():
            continue
        for path in library_dir.rglob("*"):
            if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
                all_files.append((path, library_dir))
                total += 1
    all_files.sort(key=lambda item: str(item[0]))

    _update_progress(total_files=total, phase="scanning")
    current_paths = {sanitize_text(str(path.resolve())) or "" for path, _ in all_files}

    # Phase 1.5: Auto-register subfolders that contain videos as LibraryFolder entries
    existing_folder_paths = {f.path for f in session.scalars(select(LibraryFolder)).all()}
    dirs_with_videos: set[str] = set()
    for file_path, _ in all_files:
        parent = str(file_path.resolve().parent)
        if parent not in existing_folder_paths and parent not in dirs_with_videos:
            dirs_with_videos.add(parent)

    for dir_path in sorted(dirs_with_videos):
        p = Path(dir_path)
        session.add(LibraryFolder(
            path=sanitize_text(dir_path),
            display_name=sanitize_text(p.name),
            enabled=True,
        ))
    if dirs_with_videos:
        session.commit()
        # Refresh library_dirs to include new folders
        folders = session.scalars(
            select(LibraryFolder).where(LibraryFolder.enabled == True)  # noqa: E712
        ).all()
        library_dirs = [Path(f.path) for f in folders]

    # Build lookup of existing videos by path for fast dedup
    all_videos = session.scalars(select(Video)).all()
    existing_by_path: dict[str, Video] = {v.original_path: v for v in all_videos}
    moved_lookup = build_move_lookup(all_videos)

    # Tag cache
    tag_cache: dict[str, Tag] = {}

    # IDs of videos that need browser-friendly conversion (e.g. WMV).
    # Enqueued at the end of the scan, after final commit, to avoid races.
    pending_conversion_ids: list[str] = []

    for path, library_dir in all_files:
        scanned_files += 1
        resolved = str(path.resolve())
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            # File vanished between directory listing and stat (moved, deleted,
            # network share hiccup). Skip — next scan will pick it up if it returns.
            continue
        current_size = stat.st_size
        current_mtime = stat.st_mtime

        existing = existing_by_path.get(resolved)

        needs_metadata = (
            force_metadata
            or existing is None
            or existing.file_mtime is None
            or existing.file_size != current_size
            or abs((existing.file_mtime or 0) - current_mtime) > 0.5
            or existing.duration is None
        )

        if existing and not needs_metadata:
            lib_path = sanitize_text(str(library_dir.resolve()))
            if existing.library_path != lib_path:
                existing.library_path = lib_path
                updated += 1
                batch_count += 1
            else:
                unchanged += 1
            _apply_folder_tags(session, existing, path, library_dir, tag_cache, library_dirs)
            _update_progress(processed=scanned_files)
            if batch_count >= BATCH_SIZE:
                session.commit()
                batch_count = 0
            continue

        metadata = {}
        if needs_metadata:
            try:
                metadata = extract_video_metadata(path)
            except FileNotFoundError:
                ffprobe_is_available = False
            except RuntimeError:
                metadata = {}

        if existing is None:
            existing = find_moved_video_candidate(
                moved_lookup,
                path.name,
                current_size,
                metadata.get("duration"),
            )
            if existing is not None:
                previous_path = existing.original_path
                if previous_path in existing_by_path:
                    del existing_by_path[previous_path]
                existing_by_path[resolved] = existing

        payload = {
            "title": sanitize_text(build_title(path)),
            "original_filename": sanitize_text(path.name),
            "original_path": sanitize_text(resolved),
            "library_path": sanitize_text(str(library_dir.resolve())),
            "file_size": current_size,
            "file_mtime": current_mtime,
            "duration": metadata.get("duration"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "codec": sanitize_text(metadata.get("codec")),
            "is_vertical": metadata.get("is_vertical", False),
        }

        if existing is None:
            video = Video(**payload)
            if needs_conversion(path):
                video.convert_status = "pending"
            session.add(video)
            session.flush()
            try:
                thumbnail = generate_thumbnail(path, video.id, payload["duration"])
                video.thumbnail_path = str(thumbnail)
            except Exception:
                video.thumbnail_path = None
            existing_by_path[resolved] = video
            _apply_folder_tags(session, video, path, library_dir, tag_cache, library_dirs)
            created += 1
            if needs_conversion(path):
                pending_conversion_ids.append(video.id)
        else:
            # Source content changed (size/mtime differ) → cached conversion is stale.
            # Drop the old converted file and let the worker regenerate it if still needed.
            if needs_metadata and existing.converted_path:
                try:
                    Path(existing.converted_path).unlink()
                except (OSError, FileNotFoundError):
                    pass
                existing.converted_path = None
                existing.convert_status = "none"
                existing.convert_progress = 0.0

            for key, value in payload.items():
                setattr(existing, key, value)

            # Re-queue conversion if the (now-updated) file still needs it.
            if needs_metadata and needs_conversion(path) and existing.convert_status in ("none", "failed"):
                existing.convert_status = "pending"
                existing.convert_progress = 0.0
                pending_conversion_ids.append(existing.id)

            if needs_metadata or not existing.thumbnail_path:
                try:
                    thumbnail = generate_thumbnail(path, existing.id, payload["duration"])
                    existing.thumbnail_path = str(thumbnail)
                except Exception:
                    pass
            _apply_folder_tags(session, existing, path, library_dir, tag_cache, library_dirs)
            updated += 1

        batch_count += 1
        _update_progress(processed=scanned_files, created=created)

        if batch_count >= BATCH_SIZE:
            session.commit()
            batch_count = 0

    # Soft-delete stale records whose files disappeared from scanned libraries.
    scanned_roots = [library_dir.resolve() for library_dir in library_dirs if library_dir.exists()]
    for video in all_videos:
        if video.deleted_at is not None or not video.original_path:
            continue

        video_path = Path(video.original_path)
        try:
            in_scanned_root = any(video_path.is_relative_to(root) for root in scanned_roots)
        except AttributeError:
            in_scanned_root = any(str(video_path).startswith(str(root)) for root in scanned_roots)

        if not in_scanned_root:
            continue

        normalized_path = sanitize_text(video.original_path) or ""
        if normalized_path in current_paths:
            continue

        if not video_path.exists():
            video.deleted_at = datetime.now(timezone.utc)
            updated += 1

    # Final commit
    session.commit()

    # Enqueue browser-friendly conversion for any new WMV/etc files we found.
    # Worker is started by the FastAPI lifespan; here we just push IDs.
    for video_id in pending_conversion_ids:
        try:
            enqueue_convert(video_id)
        except Exception:
            pass
    if pending_conversion_ids:
        try:
            start_convert_worker()
        except RuntimeError:
            # No running event loop in this context; the worker started by
            # the lifespan will pick up the queued items on its own.
            pass

    _update_progress(running=False, phase="done", processed=scanned_files)

    return {
        "scanned_files": scanned_files,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": 0,
        "ffprobe_available": ffprobe_is_available,
    }


def _apply_folder_tags(
    session: Session,
    video: Video,
    file_path: Path,
    library_dir: Path,
    tag_cache: dict[str, Tag],
    all_library_dirs: list[Path],
) -> None:
    """Auto-tag a video with folder names from registered ancestor libraries.

    For a video at D:\\Videos\\Foreign\\Russian\\Alice\\clip.wmv with registered
    folders [D:\\Videos, D:\\Videos\\Foreign, D:\\Videos\\Foreign\\Russian\\Alice]:
    Tags: "videos", "foreign", "alice"
    Also tags with subfolder names between library_dir and the file.
    """
    file_resolved = file_path.resolve()
    seen: set[str] = set()
    folder_names: list[str] = []

    # 1) Collect names of ALL registered library folders that are ancestors of this file
    for lib_dir in all_library_dirs:
        lib_resolved = lib_dir.resolve()
        try:
            file_resolved.relative_to(lib_resolved)
            # This folder is an ancestor — add its name as a tag
            name = lib_resolved.name.lower().strip()
            name = sanitize_text(name) or ""
            if name and name not in seen:
                folder_names.append(name)
                seen.add(name)
        except ValueError:
            pass

    # 2) Also add subfolder names within the library_dir (for unregistered intermediate folders)
    try:
        relative = file_resolved.relative_to(library_dir.resolve())
        for part in relative.parts[:-1]:  # Exclude filename
            name = part.lower().strip()
            name = sanitize_text(name) or ""
            if name and name not in seen:
                folder_names.append(name)
                seen.add(name)
    except ValueError:
        pass

    if not folder_names:
        return

    existing_tag_names = {t.name for t in video.tag_objects}
    for name in folder_names:
        if name in existing_tag_names:
            continue
        tag = tag_cache.get(name)
        if not tag:
            tag = session.scalar(select(Tag).where(Tag.name == name))
            if not tag:
                tag = Tag(name=name)
                session.add(tag)
                session.flush()
            tag_cache[name] = tag
        video.tag_objects.append(tag)
        existing_tag_names.add(name)


def build_title(path: Path) -> str:
    stem = path.stem.replace("_", " ").strip()
    stem = re.sub(r"\s+", " ", stem)
    return sanitize_text(stem or path.name) or "Untitled"
