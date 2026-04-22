from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Tag, Video, video_tags
from ..schemas import TagCreate, TagOut


router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
def list_tags(db: Session = Depends(get_db)) -> list[TagOut]:
    # Count only links to live videos — soft-deleted rows shouldn't inflate
    # the tag count or the count shown in the sidebar filter.
    rows = db.execute(
        select(Tag.id, Tag.name, func.count(Video.id))
        .outerjoin(video_tags, Tag.id == video_tags.c.tag_id)
        .outerjoin(Video, (Video.id == video_tags.c.video_id) & Video.deleted_at.is_(None))
        .group_by(Tag.id, Tag.name)
        .order_by(Tag.name)
    ).all()
    return [TagOut(id=r[0], name=r[1], video_count=r[2]) for r in rows]


@router.post("", response_model=TagOut, status_code=201)
def create_tag(payload: TagCreate, db: Session = Depends(get_db)) -> TagOut:
    name = payload.name.strip().lower()
    if not name:
        raise HTTPException(400, "Tag name cannot be empty")
    existing = db.scalar(select(Tag).where(Tag.name == name))
    if existing:
        raise HTTPException(409, f"Tag '{name}' already exists")
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return TagOut(id=tag.id, name=tag.name, video_count=0)


@router.delete("/{tag_id}", status_code=204)
def delete_tag(tag_id: int, db: Session = Depends(get_db)) -> None:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "Tag not found")
    db.delete(tag)
    db.commit()
