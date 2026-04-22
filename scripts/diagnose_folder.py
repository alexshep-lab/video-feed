"""Diagnose a folder: disk vs DB vs soft-deleted vs tag.

Usage:
    python scripts/diagnose_folder.py "L:\\Prvt\\Foreign" "strap lezz dildo"
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make backend importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from backend.database import SessionLocal
from backend.models import Tag, Video, video_tags
from backend.services.metadata import SUPPORTED_EXTENSIONS


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    folder = Path(sys.argv[1])
    tag_name = sys.argv[2].lower() if len(sys.argv) > 2 else None

    # Files on disk (recursive, only video extensions)
    if not folder.exists():
        print(f"[ERROR] Folder does not exist: {folder}")
        sys.exit(2)
    disk_files = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    disk_paths = {str(p.resolve()).lower() for p in disk_files}

    print(f"\n=== Folder: {folder} ===")
    print(f"Files on disk (video extensions, recursive): {len(disk_files)}")
    if len(disk_files) <= 5:
        for p in disk_files:
            print(f"    {p.name}")

    # DB rows under this folder
    with SessionLocal() as session:
        folder_prefix = str(folder.resolve())
        all_rows = session.scalars(
            select(Video).where(Video.original_path.ilike(f"{folder_prefix}%"))
        ).all()

        live = [r for r in all_rows if r.deleted_at is None]
        soft_deleted = [r for r in all_rows if r.deleted_at is not None]
        unconfirmed = [r for r in live if not r.confirmed]

        print(f"\n=== DB under {folder_prefix}: ===")
        print(f"Total rows:            {len(all_rows)}")
        print(f"  live (deleted_at IS NULL):  {len(live)}")
        print(f"  soft-deleted:               {len(soft_deleted)}")
        print(f"  of live, unconfirmed:       {len(unconfirmed)}")

        # File-level diff: which disk files are NOT in DB?
        db_paths = {str(Path(r.original_path).resolve()).lower() for r in all_rows}
        disk_only = disk_paths - db_paths
        db_only = db_paths - disk_paths
        print(f"\n=== Disk ↔ DB diff ===")
        print(f"On disk but not in DB:       {len(disk_only)}")
        print(f"In DB but not on disk:       {len(db_only)}  (soft-deleted candidates)")
        if disk_only and len(disk_only) <= 10:
            print("  Examples on disk, not in DB:")
            for p in list(disk_only)[:10]:
                print(f"    {p}")
        if db_only and len(db_only) <= 10:
            print("  Examples in DB, file gone:")
            for p in list(db_only)[:10]:
                print(f"    {p}")

        # Tag filter
        if tag_name:
            tag = session.scalar(select(Tag).where(Tag.name == tag_name))
            if not tag:
                print(f"\n[!] Tag '{tag_name}' not found in DB")
            else:
                with_tag_live = session.scalars(
                    select(Video)
                    .join(video_tags, Video.id == video_tags.c.video_id)
                    .where(
                        video_tags.c.tag_id == tag.id,
                        Video.deleted_at.is_(None),
                    )
                ).all()
                with_tag_all = session.scalars(
                    select(Video)
                    .join(video_tags, Video.id == video_tags.c.video_id)
                    .where(video_tags.c.tag_id == tag.id)
                ).all()
                print(f"\n=== Tag '{tag_name}' (id={tag.id}) ===")
                print(f"Videos with this tag (all):    {len(with_tag_all)}")
                print(f"Videos with this tag (live):   {len(with_tag_live)}")
                print(f"Of live-with-tag under folder: "
                      f"{sum(1 for v in with_tag_live if str(Path(v.original_path).resolve()).lower().startswith(folder_prefix.lower()))}")

                # How many live files UNDER folder DON'T have the tag?
                live_under_folder_ids = {v.id for v in live}
                tagged_ids = {v.id for v in with_tag_live}
                untagged_under_folder = live_under_folder_ids - tagged_ids
                print(f"Live files under folder w/o tag: {len(untagged_under_folder)}")

                # Does every tagged video's library_path belong to an
                # enabled LibraryFolder? If not, the list endpoint silently
                # filters them out.
                from backend.models import LibraryFolder as _LF
                enabled = {
                    lf.path
                    for lf in session.scalars(
                        select(_LF).where(_LF.enabled == True)  # noqa: E712
                    ).all()
                }
                by_lib: dict[str, int] = {}
                for v in with_tag_live:
                    by_lib[v.library_path or "<None>"] = by_lib.get(v.library_path or "<None>", 0) + 1
                print(f"\nTag '{tag_name}' library_path distribution:")
                for lp, cnt in sorted(by_lib.items(), key=lambda kv: -kv[1]):
                    marker = "  OK " if lp in enabled else " DISABLED/MISSING"
                    print(f"  {marker}  {cnt:5d}   {lp}")
                missing_lp = sum(c for lp, c in by_lib.items() if lp not in enabled)
                print(f"\nVideos with tag whose library_path is NOT enabled: {missing_lp}")


if __name__ == "__main__":
    main()
