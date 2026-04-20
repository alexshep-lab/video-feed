"""Scan library roots for screenshot/pack folders and recycle them.

Screenshot packs (folders named ``Screens``, ``_SCREENSHOTS``, ``*_scr``,
``*Pack_scr`` etc.) are carry-overs from how the library was originally
downloaded. The app never reads them — they're dead weight on disk. This
module detects them and feeds them to the Windows Recycle Bin (via the
existing ``fileops.move_to_recycle_bin``) so the user gets undo-ability.

Rules intentionally do NOT overlap with ``tag_normalize``'s SERVICE_FOLDER_NAMES:
we don't want to delete ``Incoming``, ``Converted``, ``Squized`` — those are
real staging/output buckets. Only screenshot-shaped names are in scope.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .fileops import move_to_recycle_bin

logger = logging.getLogger("videofeed.screen_cleanup")


# Exact folder names (post-normalization — lowercased, underscores → spaces,
# trailing "(...)" stripped). Keeps the list tight so legitimate categories
# like "incoming" / "converted" aren't accidentally in scope.
_SCREEN_EXACT: frozenset[str] = frozenset({
    "screen", "screens", "screens all",
    "screenshot", "screenshots",
    "screenlist", "screenlists", "screenlists all",
})

# Trailing token forms for pack folders (e.g. "Adria Rae Pack_scr",
# "Sophie_Lynx_scr", "Anissa Kate onlyfans screens"). Must be at the tail.
_SCREEN_SUFFIX_RE = re.compile(
    r"\s+(?:pack\s+)?(?:scr|screens?|screenshots?)$",
    re.IGNORECASE,
)

# Strip a trailing "(...)" cluster so "Screens (all)" or "Screenlists (all)"
# match the exact set without separate entries.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def is_screenshot_folder(folder_name: str) -> bool:
    """True if ``folder_name`` looks like a screenshot/pack folder.

    Matches ``Screens``, ``_SCREENSHOTS``, ``Screens_all``, ``Screens (all)``,
    ``Adria Rae Pack_scr``, ``Sophie_Lynx_scr``, ``onlyfans screens``, etc.
    Does NOT match ``Incoming``, ``Converted``, or normal studio names.
    """
    s = folder_name.strip().replace("_", " ")
    s = _TRAILING_PAREN_RE.sub("", s).strip()
    if not s:
        return False
    s_lower = s.lower()
    if s_lower in _SCREEN_EXACT:
        return True
    if _SCREEN_SUFFIX_RE.search(s_lower):
        return True
    return False


def _folder_size_and_count(root: Path) -> tuple[int, int]:
    """Sum sizes + file count under ``root``. Returns (0, 0) if errors."""
    total = 0
    files = 0
    try:
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
                    files += 1
            except OSError:
                continue
    except OSError:
        pass
    return total, files


def find_screenshot_folders(library_roots: list[Path]) -> dict:
    """Walk every library root, return matched screenshot folders with sizes.

    Deduped by resolved path — if the same folder is reachable via two
    different registered library roots, it only appears once. Nested matches
    are suppressed: ``Alice/screens/onlyfans screens`` reports only the
    outer ``Alice/screens`` (recycling it drops the inner one too).
    """
    seen: set[Path] = set()
    items: list[dict] = []
    total_size = 0

    # Sort roots by path length so shallow roots are visited first; lets us
    # skip subtree walks we've already covered.
    sorted_roots = sorted({r.resolve() for r in library_roots if r.exists() and r.is_dir()},
                          key=lambda p: len(str(p)))

    # Track "outer" screen folders; anything inside one of these is pruned.
    outer_paths: list[Path] = []

    def is_inside_outer(candidate: Path) -> bool:
        return any(
            candidate == outer or outer in candidate.parents
            for outer in outer_paths
        )

    for root in sorted_roots:
        try:
            for entry in root.rglob("*"):
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                resolved = entry.resolve()
                if resolved in seen or is_inside_outer(resolved):
                    continue
                if not is_screenshot_folder(entry.name):
                    continue
                size, files = _folder_size_and_count(resolved)
                items.append({
                    "path": str(resolved),
                    "name": entry.name,
                    "size": size,
                    "file_count": files,
                })
                total_size += size
                seen.add(resolved)
                outer_paths.append(resolved)
        except OSError as exc:
            logger.warning("Failed to walk %s: %s", root, exc)
            continue

    items.sort(key=lambda it: -it["size"])  # biggest first — easier to skim
    return {
        "count": len(items),
        "total_size": total_size,
        "items": items,
    }


def purge_screenshot_folders(paths: list[str], library_roots: list[Path]) -> dict:
    """Recycle-bin the given folders. Safety: must live inside a library root.

    Silently skips paths outside any registered library root — defense in
    depth against UI / client sending stale or crafted paths.
    """
    resolved_roots = [r.resolve() for r in library_roots if r.exists()]

    recycled = 0
    failed = 0
    bytes_freed = 0
    errors: list[dict] = []

    for raw in paths:
        try:
            cand = Path(raw).resolve()
        except (OSError, RuntimeError):
            failed += 1
            errors.append({"path": raw, "error": "resolve_failed"})
            continue

        inside_library = any(
            cand == root or root in cand.parents
            for root in resolved_roots
        )
        if not inside_library:
            failed += 1
            errors.append({"path": str(cand), "error": "outside_library_roots"})
            continue

        if not cand.exists() or not cand.is_dir():
            failed += 1
            errors.append({"path": str(cand), "error": "not_a_directory"})
            continue

        size, _ = _folder_size_and_count(cand)
        try:
            move_to_recycle_bin(cand)
            recycled += 1
            bytes_freed += size
        except Exception as exc:
            failed += 1
            errors.append({"path": str(cand), "error": str(exc)})

    logger.info(
        "Screen-cleanup: recycled=%d failed=%d freed=%d bytes",
        recycled, failed, bytes_freed,
    )
    return {
        "recycled": recycled,
        "failed": failed,
        "total_bytes_freed": bytes_freed,
        "errors": errors[:30],
    }
