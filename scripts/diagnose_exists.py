"""Report every live Video row whose original_path does not stat().

Useful when you suspect the ``os.path.exists()`` check used by
``/maintenance/missing-files`` is disagreeing with reality — particularly
on Windows with unusual Unicode characters in filenames.

Usage:
    python scripts/diagnose_exists.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from backend.database import SessionLocal
from backend.models import Video


def main() -> None:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Video).where(Video.deleted_at.is_(None))
        ).all()

    total = len(rows)
    path_missing: list[Video] = []
    path_odd: list[tuple[str, Exception]] = []

    for v in rows:
        if not v.original_path:
            path_missing.append(v)
            continue
        try:
            # Two independent checks — sometimes exists() returns stale data
            # for strange paths but stat() raises FileNotFoundError honestly.
            if not os.path.exists(v.original_path):
                path_missing.append(v)
                continue
            # Sanity: also stat via pathlib to double-check on Windows.
            Path(v.original_path).stat()
        except Exception as e:  # noqa: BLE001
            path_odd.append((v.original_path, e))

    print(f"Live rows scanned: {total}")
    print(f"os.path.exists()=False:  {len(path_missing)}")
    print(f"stat raised exception:   {len(path_odd)}")

    if path_missing:
        print("\n=== MISSING (first 20) ===")
        for v in path_missing[:20]:
            print(f"  {v.id}  {v.original_path!r}")
    if path_odd:
        print("\n=== ODD (stat failures, first 20) ===")
        for p, err in path_odd[:20]:
            print(f"  {p!r}\n    -> {type(err).__name__}: {err}")


if __name__ == "__main__":
    main()
