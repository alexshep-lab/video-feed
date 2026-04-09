"""Duplicate detection service.

Two strategies:
1. Exact: same file_size + similar duration (within 2 seconds)
2. Perceptual: same phash (computed from thumbnail)
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Video
from .thumbnail import generate_thumbnail


def file_sha1(path: Path, chunk_size: int = 1024 * 1024) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def find_size_duration_duplicates(session: Session) -> list[list[Video]]:
    """Find exact duplicates by file content hash, using file_size as a prefilter."""
    videos = session.scalars(
        select(Video).where(Video.deleted_at.is_(None))
    ).all()

    size_groups: dict[int, list[Video]] = defaultdict(list)
    for v in videos:
        if not Path(v.original_path).exists():
            continue
        if not v.file_size:
            continue
        size_groups[v.file_size].append(v)

    groups: list[list[Video]] = []
    for same_size_videos in size_groups.values():
        if len(same_size_videos) < 2:
            continue

        hash_groups: dict[str, list[Video]] = defaultdict(list)
        for video in same_size_videos:
            digest = file_sha1(Path(video.original_path))
            if digest:
                hash_groups[digest].append(video)

        groups.extend(group for group in hash_groups.values() if len(group) > 1)

    return groups


def compute_phash_for_video(video: Video) -> str | None:
    """Compute a simple average-hash from the thumbnail file.

    Uses a small grayscale grid (8x8) and bit-encoded against the mean.
    """
    try:
        thumb = generate_thumbnail(Path(video.original_path), video.id, video.duration)
    except Exception:
        return None

    if not thumb.exists():
        return None

    # Read JPEG bytes and hash by md5 of downscaled grayscale buckets
    # We don't have PIL available necessarily, so we use a content fingerprint
    # via ffmpeg as a fallback if PIL is missing.
    try:
        from PIL import Image
        img = Image.open(thumb).convert("L").resize((8, 8))
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p > avg else "0" for p in pixels)
        return f"{int(bits, 2):016x}"
    except ImportError:
        # Fallback: md5 of the thumbnail file itself (less perceptual but still useful)
        with open(thumb, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:16]


def hamming_distance(h1: str, h2: str) -> int:
    if not h1 or not h2 or len(h1) != len(h2):
        return 64
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except ValueError:
        return 64 if h1 != h2 else 0


def find_phash_duplicates(session: Session, threshold: int = 5) -> list[list[Video]]:
    """Group videos with similar phash (Hamming distance <= threshold)."""
    videos = session.scalars(
        select(Video).where(Video.deleted_at.is_(None), Video.phash.isnot(None))
    ).all()
    videos = [video for video in videos if Path(video.original_path).exists()]

    groups: list[list[Video]] = []
    used: set[str] = set()
    for i, v1 in enumerate(videos):
        if v1.id in used:
            continue
        group = [v1]
        for v2 in videos[i + 1:]:
            if v2.id in used:
                continue
            if hamming_distance(v1.phash, v2.phash) <= threshold:
                group.append(v2)
                used.add(v2.id)
        if len(group) > 1:
            for v in group:
                used.add(v.id)
            groups.append(group)
    return groups


def compute_all_phashes(session: Session, only_missing: bool = True) -> int:
    """Compute phash for all videos that don't have one yet."""
    statement = select(Video).where(Video.deleted_at.is_(None))
    if only_missing:
        statement = statement.where(Video.phash.is_(None))
    videos = session.scalars(statement).all()

    count = 0
    for v in videos:
        h = compute_phash_for_video(v)
        if h:
            v.phash = h
            count += 1
            if count % 50 == 0:
                session.commit()
    session.commit()
    return count
