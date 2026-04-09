from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import LibraryFolder


router = APIRouter(prefix="/settings", tags=["settings"])


class LibrarySettings(BaseModel):
    library_dirs: list[str] = Field(default_factory=list)


@router.get("/libraries", response_model=LibrarySettings)
def get_library_settings(db: Session = Depends(get_db)) -> LibrarySettings:
    """Return library dirs from DB (LibraryFolder table)."""
    folders = db.scalars(select(LibraryFolder).order_by(LibraryFolder.path)).all()
    if folders:
        return LibrarySettings(library_dirs=[f.path for f in folders])
    # Fallback: return config-based dirs if DB has no folders yet
    settings = get_settings()
    return LibrarySettings(library_dirs=[str(p) for p in settings.library_dirs])


@router.put("/libraries", response_model=LibrarySettings)
def update_library_settings(payload: LibrarySettings, db: Session = Depends(get_db)) -> LibrarySettings:
    """Sync library folders from the UI textarea.

    Adds new paths, keeps existing ones. Does NOT remove folders
    that are missing from the payload (use DELETE /api/libraries/{id} for that).
    """
    existing = db.scalars(select(LibraryFolder)).all()
    existing_paths = {f.path for f in existing}

    for raw_path in payload.library_dirs:
        cleaned = raw_path.strip()
        if not cleaned:
            continue
        resolved = str(Path(cleaned).resolve())
        if resolved not in existing_paths:
            folder = LibraryFolder(
                path=resolved,
                display_name=Path(resolved).name,
                enabled=True,
            )
            db.add(folder)
            existing_paths.add(resolved)

    db.commit()

    all_folders = db.scalars(select(LibraryFolder).order_by(LibraryFolder.path)).all()
    return LibrarySettings(library_dirs=[f.path for f in all_folders])
