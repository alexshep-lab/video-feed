"""Tag name normalization + dedup/cleanup.

The scanner originally turned every folder name into a tag via
``folder.lower().strip()``. That leaked a lot of junk into the DB:

  - count suffixes:   ``"Alice (66)"`` and ``"Alice (12)"`` became two tags
  - site suffixes:    ``"GuysForMatures.com"`` vs ``"GuysForMatures"``
  - screen packs:     ``"Sophie_Lynx_scr"``, ``"Adria Rae Pack_scr"``
  - service folders:  ``"screens"``, ``"incoming"``, ``"_SCREENSHOTS"``

``normalize_tag_name()`` is the one place we decide "what does a folder name
mean as a tag". It's pure — no DB, no filesystem — so both the scanner (on
write) and the one-shot migration (retroactively fixing existing rows) can
share it. If both call the same function, scans stay idempotent with the
post-migration DB state.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import delete, func, literal, select
from sqlalchemy.orm import Session

from ..models import Tag, video_tags

logger = logging.getLogger("videofeed.tag_normalize")


# Folder names that mean "this is a staging / screenshot / derived-output
# area", not a semantic category. The scanner should not build a tag from
# them, and the migration should strip them off existing rows.
#
# Entries are compared against the *post-normalization* string (lowercased,
# underscores already converted to spaces, count/site/screen-pack suffixes
# already stripped). So "Screens_all" becomes "screens all" before the
# lookup — the blacklist entry must use the space-form to match.
SERVICE_FOLDER_NAMES: frozenset[str] = frozenset({
    # Screenshots / contact sheets produced by other tools
    "screen", "screens", "screens all", "screens (all)",
    "screenshot", "screenshots",
    "screenlist", "screenlists", "screenlists all",
    # Incoming / staging buckets
    "incoming", "staging", "inbox",
    # Our own derived outputs
    "squized", "compressed", "converted", "big",
    # Generic carrier names that don't describe content
    "video", "videos", "clips", "clip",
    # Unspecific quality markers on their own
    "sd",
})

# Trailing markers attached to screenshot-pack folders. Underscores get
# normalized to spaces *before* this regex runs, so we match on spaces.
_TRAILING_SCREENPACK_RE = re.compile(
    r"\s+(?:hevc\s+)?pack\s+(?:scr|screens?)$"
    r"|\s+scr$"
    r"|\s+screens?$"
    r"|\s+screenshots?$",
    re.IGNORECASE,
)

# "anissa kate.com" → "anissa kate". Match only at the tail.
_SITE_SUFFIX_RE = re.compile(r"\.(?:com|net|tv|org|xxx|co\.uk)\s*$", re.IGNORECASE)

# Bracketed site prefix: "[PornHubPremium.com] Alice" → "Alice"
_BRACKET_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*")

# Trailing "(...)" — typically a count like "(66)" or a year like "(2022)"
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")

# Multiple spaces / leading-trailing
_MULTI_WS_RE = re.compile(r"\s+")

# Trailing standalone number after a space (e.g. "Alice 66" → "Alice")
_TRAILING_NUMBER_RE = re.compile(r"\s+\d+\s*$")

# Pure-punctuation-only tail
_TRAIL_PUNCT_RE = re.compile(r"[\s\-_.]+$")


def normalize_tag_name(raw: str | None) -> str | None:
    """Canonicalize a folder name into a tag, or return None to reject it.

    Returns ``None`` when the input is a service folder (screens, incoming,
    ...), empty after stripping, pure digits, or a single character.

    The output is always lower-cased and whitespace-collapsed. Callers who
    need to upsert a Tag row should check for ``None`` first and skip.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None

    # Lead: drop "[Site] " prefix first — the site label inside brackets is
    # almost never useful as a tag.
    s = _BRACKET_PREFIX_RE.sub("", s)
    # Tail: drop "(66)" / "(2022)" / "(161)" — studio count or year markers.
    s = _TRAILING_PAREN_RE.sub("", s)
    # Underscores are just a spacing convention in folder names — make them
    # real spaces so the downstream regexes can match token boundaries.
    s = s.replace("_", " ")
    # "GuysForMatures.com" → "GuysForMatures". Must happen before trailing
    # number / screen-pack stripping, since the dotted suffix attaches at end.
    s = _SITE_SUFFIX_RE.sub("", s)
    # "Adria Rae Pack scr" → "Adria Rae"
    s = _TRAILING_SCREENPACK_RE.sub("", s)
    s = _MULTI_WS_RE.sub(" ", s).strip()
    # "Alice 66" with a standalone trailing number — strip it. Done after
    # screen-pack removal so we don't accidentally eat digits that are part
    # of e.g. "Adria Rae 2022" (well, we do strip — probably desired).
    s = _TRAILING_NUMBER_RE.sub("", s).strip()
    s = _TRAIL_PUNCT_RE.sub("", s).strip()

    lowered = s.lower()
    if not lowered:
        return None
    if lowered in SERVICE_FOLDER_NAMES:
        return None
    if lowered.isdigit():
        return None
    if len(lowered) < 2:
        return None
    return lowered


# ---- DB-level migration: fold existing tags through normalize_tag_name ----


def _group_tags_by_normalized(session: Session) -> tuple[dict[str, list[Tag]], list[Tag]]:
    """Bucket every Tag by its normalized name.

    Returns (groups, to_delete) where:
      - groups: normalized → list[Tag] (possibly multiple equivalent tags)
      - to_delete: tags whose normalized form is None (service folders etc.)
    """
    groups: dict[str, list[Tag]] = {}
    to_delete: list[Tag] = []
    for tag in session.scalars(select(Tag)).all():
        norm = normalize_tag_name(tag.name)
        if norm is None:
            to_delete.append(tag)
        else:
            groups.setdefault(norm, []).append(tag)
    return groups, to_delete


def plan_tag_normalization(session: Session) -> dict:
    """Compute what normalize would do, without touching the DB."""
    groups, to_delete = _group_tags_by_normalized(session)

    video_counts: dict[int, int] = {
        row[0]: row[1]
        for row in session.execute(
            select(video_tags.c.tag_id, func.count().label("n"))
            .group_by(video_tags.c.tag_id)
        ).all()
    }

    deletes: list[dict] = []
    for tag in to_delete:
        deletes.append({
            "name": tag.name,
            "videos": video_counts.get(tag.id, 0),
        })

    renames: list[dict] = []
    merges: list[dict] = []
    for norm, tags in groups.items():
        if len(tags) == 1:
            t = tags[0]
            if t.name != norm:
                renames.append({
                    "from": t.name,
                    "to": norm,
                    "videos": video_counts.get(t.id, 0),
                })
        else:
            merges.append({
                "canonical": norm,
                "sources": sorted([t.name for t in tags]),
                "videos_affected": sum(video_counts.get(t.id, 0) for t in tags),
            })

    total_tag_count = len(to_delete) + sum(len(v) for v in groups.values())
    final_tag_count = len(groups)
    return {
        "dry_run": True,
        "total_tags_before": total_tag_count,
        "total_tags_after": final_tag_count,
        "deletes": sorted(deletes, key=lambda d: -d["videos"]),
        "merges": sorted(merges, key=lambda m: -m["videos_affected"]),
        "renames": sorted(renames, key=lambda r: -r["videos"]),
    }


def apply_tag_normalization(session: Session) -> dict:
    """Actually perform the rename/merge/delete plan.

    All mutations happen in a single transaction: either the DB ends up fully
    normalized, or nothing changes (callers can retry). The underlying SQLite
    UNIQUE constraint on ``(video_id, tag_id)`` is sidestepped via
    ``INSERT OR IGNORE`` when remapping.
    """
    groups, to_delete = _group_tags_by_normalized(session)

    deleted = 0
    deleted_links = 0
    renamed = 0
    merged_tags = 0
    remapped_links = 0

    # 1) Merge / rename every non-None group.
    #
    # For multi-tag groups we pick a canonical row (prefer the one whose name
    # already matches the normalized form — if none match, the first by id).
    # All `video_tags` rows pointing at sources get redirected to canonical
    # via `INSERT OR IGNORE` (so duplicates collapse) then the source rows
    # are deleted. Finally the canonical row's name is set to the normalized
    # form.
    for norm, tags in groups.items():
        tags_sorted = sorted(tags, key=lambda t: (t.name != norm, t.id))
        canonical = tags_sorted[0]
        sources = tags_sorted[1:]

        for src in sources:
            # Count source links BEFORE remapping — INSERT OR IGNORE won't
            # tell us how many rows were actually written.
            src_link_count = session.scalar(
                select(func.count()).where(video_tags.c.tag_id == src.id)
            ) or 0
            remapped_links += src_link_count

            insert_stmt = video_tags.insert().prefix_with("OR IGNORE").from_select(
                ["video_id", "tag_id"],
                select(video_tags.c.video_id, literal(canonical.id))
                .where(video_tags.c.tag_id == src.id),
            )
            session.execute(insert_stmt)
            session.execute(delete(video_tags).where(video_tags.c.tag_id == src.id))
            session.delete(src)
            merged_tags += 1

        if canonical.name != norm:
            canonical.name = norm
            renamed += 1

    # 2) Drop service-folder tags entirely (screens, incoming, ...).
    for tag in to_delete:
        link_count = session.scalar(
            select(func.count()).where(video_tags.c.tag_id == tag.id)
        ) or 0
        deleted_links += link_count
        session.execute(delete(video_tags).where(video_tags.c.tag_id == tag.id))
        session.delete(tag)
        deleted += 1

    session.commit()
    logger.info(
        "Tag normalize: renamed=%d merged=%d (links_remapped=%d) deleted=%d (links_dropped=%d)",
        renamed, merged_tags, remapped_links, deleted, deleted_links,
    )
    return {
        "dry_run": False,
        "renamed": renamed,
        "merged_tags": merged_tags,
        "links_remapped": remapped_links,
        "deleted_tags": deleted,
        "links_dropped": deleted_links,
    }
