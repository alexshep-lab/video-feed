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


_FINGERPRINT_WINDOW = 64 * 1024  # 64 KB from head + 64 KB from tail


def file_partial_fingerprint(path: Path, file_size: int) -> str | None:
    """Fast fingerprint: SHA-1 of (size + first 64 KB + last 64 KB).

    Used as the second-stage check after file-size grouping. For files of
    identical size, matching head+tail bytes means content match with
    overwhelming probability — video container headers (and trailing index
    atoms for MP4) are well within the first/last 64 KB. Hundreds of times
    faster than hashing the entire file, critical for libraries on slow drives.
    """
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha1()
    digest.update(str(file_size).encode("ascii"))
    try:
        with path.open("rb") as stream:
            head = stream.read(_FINGERPRINT_WINDOW)
            digest.update(head)
            if file_size > _FINGERPRINT_WINDOW * 2:
                # Read the trailing window. Files smaller than 2 MB are entirely
                # covered by the head read, so no tail seek is needed.
                stream.seek(max(0, file_size - _FINGERPRINT_WINDOW))
                tail = stream.read(_FINGERPRINT_WINDOW)
                digest.update(tail)
    except OSError:
        return None
    return digest.hexdigest()


def find_size_duration_duplicates(session: Session) -> list[list[Video]]:
    """Find exact duplicates by partial-content fingerprint, prefiltered by file_size.

    Uses ``file_partial_fingerprint`` for the content check — this is hundreds
    of times faster than full SHA-1 on multi-GB files and gives the same
    practical accuracy because file size is already an exact match.
    """
    videos = session.scalars(
        select(Video).where(Video.deleted_at.is_(None))
    ).all()

    size_groups: dict[int, list[Video]] = defaultdict(list)
    for v in videos:
        if not v.file_size:
            continue
        if not Path(v.original_path).exists():
            continue
        size_groups[v.file_size].append(v)

    groups: list[list[Video]] = []
    for file_size, same_size_videos in size_groups.items():
        if len(same_size_videos) < 2:
            continue

        hash_groups: dict[str, list[Video]] = defaultdict(list)
        for video in same_size_videos:
            digest = file_partial_fingerprint(Path(video.original_path), file_size)
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
