from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LibraryFolder, Video
from ..schemas import LibraryFolderCreate, LibraryFolderOut, LibraryFolderUpdate


router = APIRouter(prefix="/libraries", tags=["libraries"])


@router.get("", response_model=list[LibraryFolderOut])
def list_libraries(db: Session = Depends(get_db)) -> list[LibraryFolderOut]:
    # Exclude soft-deleted videos from the count — otherwise the sidebar
    # number is higher than what the library filter actually returns.
    rows = db.execute(
        select(LibraryFolder, func.count(Video.id))
        .outerjoin(
            Video,
            (Video.library_path == LibraryFolder.path) & Video.deleted_at.is_(None),
        )
        .group_by(LibraryFolder.id)
        .order_by(LibraryFolder.path)
    ).all()
    return [
        LibraryFolderOut(
            id=row[0].id,
            path=row[0].path,
            enabled=row[0].enabled,
            is_incoming=row[0].is_incoming,
            display_name=row[0].display_name,
            video_count=row[1],
        )
        for row in rows
    ]


@router.post("", response_model=list[LibraryFolderOut], status_code=201)
def add_library(
    payload: LibraryFolderCreate,
    expand_subfolders: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> list[LibraryFolderOut]:
    """Add a library folder. If expand_subfolders=True (default), also adds
    every immediate subfolder that contains video files as separate entries."""
    root = Path(payload.path).resolve()
    if not root.is_dir():
        raise HTTPException(400, f"Directory does not exist: {root}")

    added: list[LibraryFolder] = []

    # Collect folders to add: root + all nested subfolders recursively
    folders_to_add = [root]
    if expand_subfolders:
        for child in sorted(root.rglob("*")):
            if child.is_dir():
                folders_to_add.append(child)

    existing_paths = {
        f.path
        for f in db.scalars(select(LibraryFolder)).all()
    }

    for folder_path in folders_to_add:
        resolved = str(folder_path.resolve())
        if resolved in existing_paths:
            continue
        folder = LibraryFolder(
            path=resolved,
            display_name=folder_path.name,
            enabled=True,
        )
        db.add(folder)
        added.append(folder)
        existing_paths.add(resolved)

    db.commit()

    # Return all folders with counts (live videos only).
    rows = db.execute(
        select(LibraryFolder, func.count(Video.id))
        .outerjoin(
            Video,
            (Video.library_path == LibraryFolder.path) & Video.deleted_at.is_(None),
        )
        .group_by(LibraryFolder.id)
        .order_by(LibraryFolder.path)
    ).all()
    return [
        LibraryFolderOut(
            id=row[0].id, path=row[0].path, enabled=row[0].enabled,
            display_name=row[0].display_name, video_count=row[1],
        )
        for row in rows
    ]


@router.patch("/{folder_id}", response_model=LibraryFolderOut)
def update_library(
    folder_id: int,
    payload: LibraryFolderUpdate,
    db: Session = Depends(get_db),
) -> LibraryFolderOut:
    folder = db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "Library folder not found")
    if payload.enabled is not None:
        folder.enabled = payload.enabled
    if payload.display_name is not None:
        folder.display_name = payload.display_name
    if payload.is_incoming is not None:
        # Only one incoming folder allowed — clear others if setting true
        if payload.is_incoming:
            for f in db.scalars(select(LibraryFolder).where(LibraryFolder.is_incoming == True)).all():  # noqa: E712
                f.is_incoming = False
        folder.is_incoming = payload.is_incoming
    db.commit()
    db.refresh(folder)
    count = db.scalar(
        select(func.count(Video.id)).where(
            Video.library_path == folder.path,
            Video.deleted_at.is_(None),
        )
    ) or 0
    return LibraryFolderOut(
        id=folder.id, path=folder.path, enabled=folder.enabled,
        is_incoming=folder.is_incoming,
        display_name=folder.display_name, video_count=count,
    )


@router.delete("/{folder_id}", status_code=204)
def delete_library(folder_id: int, db: Session = Depends(get_db)) -> None:
    folder = db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "Library folder not found")
    db.delete(folder)
    db.commit()
