"""Extract tags from video filenames.

Folder-based tagging already covers the studio/actress folder hierarchy
(``foreign``, ``alice``, ``guysformatures``). Filenames often carry the
SAME studio info in a differently-shaped prefix — ``stm_g536_Carol&Adam``
still means StunningMatures — plus actor names glued with ``&`` and a few
genuinely filename-only markers (quality / codec / site brackets).

Every rule is whitelist-driven: random filename noise (UUIDs, CRCs, scene
codes like ``g603``) never leaks into the tag set.

Flow: ``plan_extraction(session)`` → preview per proposed tag (count +
sample filenames). ``apply_extraction(session, selected)`` writes the
``video_tags`` rows. Existing pairs are sidestepped via ``INSERT OR IGNORE``.
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Tag, Video, video_tags
from .tag_normalize import normalize_tag_name

logger = logging.getLogger("videofeed.tag_extract")


# ---- Studio prefix at the start of the stem ----
#
# Matches the pattern the user's library actually uses:
#   stunningmatures_g603_Emilia&Arthur
#   stm_g536_Carol&Adam
#   nylonscreen_g785_clip
#   pantyhoseline_g1103_clip
# -> the first underscore-terminated alnum token is the studio / site tag.
#
# Known short forms expand via STUDIO_ABBREVIATIONS so "stm" and the full
# "stunningmatures" collapse into the same tag. Unknown prefixes pass
# through as-is (lowercased); the dedup UI can merge variants later.
_STUDIO_PREFIX_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9]{2,})_")

STUDIO_ABBREVIATIONS: dict[str, str] = {
    # Manual fallback. Folder-based auto-expansion (see
    # ``_expansion_from_folder``) usually handles this when the parent
    # directory spells the full name — e.g.
    # ``L:\Prvt\Incoming\StunningMatures\stm_g536_...`` resolves ``stm``
    # to ``stunningmatures`` without this map. Keep entries here only for
    # abbreviations whose expansion isn't in the folder tree.
    #
    # Keys/values must be lowercase.
    "stm": "stunningmatures",
}


def _is_contraction(short: str, full: str) -> bool:
    """True if every letter of ``short`` appears, in order, inside ``full``.

    ``stm`` is a contraction of ``stunningmatures``: s@0, t@1, m@8. Order
    matters — ``mts`` would NOT match ``stunningmatures`` (m comes after s
    in the source, not before). Equal-length strings or empty inputs fail.
    """
    short = short.lower()
    full = full.lower()
    if not short or len(short) >= len(full):
        return False
    source_iter = iter(full)
    return all(c in source_iter for c in short)


def _expansion_from_folder(prefix: str, folder_name: str | None) -> str | None:
    """Return folder-derived expansion of ``prefix`` when plausible, else None.

    Takes the first normalized word of the folder name and checks whether
    ``prefix`` is a letter-ordered contraction of it. Using the first word
    only means we don't get polluted by ``(11)`` / ``Full_06.05.09`` tails
    that ``normalize_tag_name`` can't fully strip.
    """
    if not folder_name:
        return None
    normalized = normalize_tag_name(folder_name)
    if not normalized:
        return None
    first_word = normalized.split(" ", 1)[0]
    if not first_word or first_word == prefix:
        return None
    if _is_contraction(prefix, first_word):
        return first_word
    return None


# ---- Actor names joined with '&' (Emilia&Arthur, Carol&Adam, ...) ----
#
# Each name must be a capitalized alphabetic token of 3+ chars. We
# intentionally require capitalization — otherwise generic joined words
# like ``clip&scene`` or ``full&uncut`` bleed into tags. If your library
# has lowercase names, rename first or ask to relax this rule.
_NAME_AMP_RE = re.compile(r"([A-Z][a-zA-Z]{2,})(?:&([A-Z][a-zA-Z]{2,}))+")


# ---- Quality / resolution ----
# `\b` doesn't treat digits-letters as a word boundary in Python regex, so
# markers like "1080p" inside "something1080pblob" still match — we use
# explicit negative lookarounds for alphanumerics to stay conservative.
_QUALITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![a-z0-9])(?:2160p|uhd|4k)(?![a-z0-9])", re.IGNORECASE), "4k"),
    (re.compile(r"(?<![a-z0-9])(?:1080p|fhd)(?![a-z0-9])", re.IGNORECASE), "1080p"),
    (re.compile(r"(?<![a-z0-9])720p(?![a-z0-9])", re.IGNORECASE), "720p"),
    (re.compile(r"(?<![a-z0-9])480p(?![a-z0-9])", re.IGNORECASE), "480p"),
]

# ---- Codec ----
_CODEC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![a-z0-9])(?:hevc|h\.?265|x265)(?![a-z0-9])", re.IGNORECASE), "hevc"),
    (re.compile(r"(?<![a-z0-9])(?:h\.?264|x264|avc)(?![a-z0-9])", re.IGNORECASE), "h264"),
    (re.compile(r"(?<![a-z0-9])av1(?![a-z0-9])", re.IGNORECASE), "av1"),
]

# ---- Bracketed metadata: sites, acronyms ----
#
# We scan every ``[...]`` cluster in the stem:
#   [SiteName.com]       → site tag (strip domain) → "sitename"
#   [PornHubPremium.com] → "pornhubpremium"
#   [OF]                 → 2–4 letter acronym, mapped via _ACRONYM_MAP; else skipped
#   [free text]          → skipped (too noisy without a whitelist)
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")
_SITE_RE = re.compile(r"^([a-z0-9][a-z0-9.\-]+?)\.(com|net|tv|org|xxx|co\.uk)$", re.IGNORECASE)

# Known acronyms worth expanding — extend as you learn your library.
_ACRONYM_MAP: dict[str, str] = {
    "of": "onlyfans",
    "ph": "pornhub",
    "mw": "manyvids",
    "mv": "manyvids",
    "xh": "xhamster",
}


def extract_tags_from_filename(
    filename: str,
    folder_hint: str | None = None,
) -> set[str]:
    """Return the set of candidate tags for a single filename's stem.

    Pure: no DB, no side effects. Empty set if nothing matches. Every output
    is already lowercased and passes ``normalize_tag_name`` (so scanner +
    extraction stay aligned).

    ``folder_hint`` is the parent directory name (not the full path). When
    given, the extractor tries to expand a short filename prefix (``stm``)
    into the folder's full form (``stunningmatures``) so the user doesn't
    need a hardcoded entry in ``STUDIO_ABBREVIATIONS`` for every studio.
    """
    stem = filename.rsplit(".", 1)[0]  # drop the extension
    tags: set[str] = set()

    # Studio prefix (first alnum token before an underscore). Expansion
    # precedence: folder-derived > manual map > prefix as-is.
    prefix_match = _STUDIO_PREFIX_RE.match(stem)
    if prefix_match:
        prefix = prefix_match.group(1).lower()
        expansion = (
            _expansion_from_folder(prefix, folder_hint)
            or STUDIO_ABBREVIATIONS.get(prefix)
            or prefix
        )
        normalized = normalize_tag_name(expansion)
        if normalized:
            tags.add(normalized)

    # Names joined with '&' — Emilia&Arthur, Carol&Adam, Alice&Bob&Carol.
    # re.finditer returns one match per &-chain but only captures the
    # last two groups, so split the whole match span on '&' ourselves.
    for match in _NAME_AMP_RE.finditer(stem):
        for part in match.group(0).split("&"):
            part = part.strip()
            if len(part) >= 3 and part.isalpha() and part[0].isupper():
                normalized = normalize_tag_name(part)
                if normalized:
                    tags.add(normalized)

    for pattern, tag in (*_QUALITY_PATTERNS, *_CODEC_PATTERNS):
        if pattern.search(stem):
            tags.add(tag)

    for match in _BRACKET_RE.finditer(stem):
        inner = match.group(1).strip()
        if not inner:
            continue
        inner_lower = inner.lower()
        site_match = _SITE_RE.match(inner_lower)
        if site_match:
            # "pornhubpremium.com" → "pornhubpremium". Strip any leading
            # non-alpha noise just in case.
            normalized = normalize_tag_name(site_match.group(1))
            if normalized:
                tags.add(normalized)
            continue
        if 2 <= len(inner_lower) <= 4 and inner_lower.isalpha():
            mapped = _ACRONYM_MAP.get(inner_lower)
            if mapped:
                tags.add(mapped)

    return tags


# ---- DB planning + application ----


def plan_extraction(session: Session, sample_size: int = 5) -> dict:
    """Dry-run: walk active videos, group proposed tags by name.

    Skips pairs (video, tag) that already exist — the preview shows net
    additions only.
    """
    existing_by_video: dict[str, set[str]] = defaultdict(set)
    for vid, tag_name in session.execute(
        select(video_tags.c.video_id, Tag.name)
        .join(Tag, Tag.id == video_tags.c.tag_id)
    ).all():
        existing_by_video[vid].add(tag_name)

    # Group proposed tag -> list of (video_id, title)
    grouped: dict[str, list[dict]] = defaultdict(list)
    total_additions = 0
    videos_touched: set[str] = set()

    for vid, title, fname, orig_path in session.execute(
        select(Video.id, Video.title, Video.original_filename, Video.original_path)
        .where(Video.deleted_at.is_(None), Video.original_filename.is_not(None))
    ).all():
        folder_hint = os.path.basename(os.path.dirname(orig_path or "")) or None
        proposed = extract_tags_from_filename(fname or "", folder_hint=folder_hint)
        if not proposed:
            continue
        already = existing_by_video.get(vid, set())
        new_tags = proposed - already
        if not new_tags:
            continue
        videos_touched.add(vid)
        for t in new_tags:
            grouped[t].append({"id": vid, "title": title, "filename": fname})
            total_additions += 1

    proposed_tags = []
    for name in sorted(grouped, key=lambda k: -len(grouped[k])):
        entries = grouped[name]
        proposed_tags.append({
            "tag": name,
            "videos": len(entries),
            "sample_videos": entries[:sample_size],
        })

    return {
        "proposed_tags": proposed_tags,
        "total_tags": len(proposed_tags),
        "total_additions": total_additions,
        "videos_touched": len(videos_touched),
    }


def apply_extraction(session: Session, tag_whitelist: list[str] | None = None) -> dict:
    """Write video_tags rows for every proposed (video, tag) pair.

    If ``tag_whitelist`` is given, only those tag names are applied — used
    by the UI when the user unchecks some proposed tags. ``None`` means
    apply everything.
    """
    allowed: set[str] | None = None
    if tag_whitelist is not None:
        allowed = {t.strip().lower() for t in tag_whitelist if t and t.strip()}
        if not allowed:
            return {
                "applied": 0, "tags_created": 0, "tags_reused": 0,
                "videos_touched": 0,
            }

    # Build once: name -> Tag row (upsert lazily below).
    tag_cache: dict[str, Tag] = {
        t.name: t for t in session.scalars(select(Tag)).all()
    }
    created = 0

    def ensure_tag(name: str) -> Tag:
        nonlocal created
        existing = tag_cache.get(name)
        if existing is not None:
            return existing
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
        tag_cache[name] = tag
        created += 1
        return tag

    applied = 0
    videos_touched: set[str] = set()

    for vid, fname, orig_path in session.execute(
        select(Video.id, Video.original_filename, Video.original_path)
        .where(Video.deleted_at.is_(None), Video.original_filename.is_not(None))
    ).all():
        folder_hint = os.path.basename(os.path.dirname(orig_path or "")) or None
        proposed = extract_tags_from_filename(fname or "", folder_hint=folder_hint)
        if allowed is not None:
            proposed &= allowed
        if not proposed:
            continue
        for name in proposed:
            tag = ensure_tag(name)
            # INSERT OR IGNORE — collides harmlessly when the link already exists.
            result = session.execute(
                video_tags.insert().prefix_with("OR IGNORE"),
                [{"video_id": vid, "tag_id": tag.id}],
            )
            if result.rowcount and result.rowcount > 0:
                applied += 1
                videos_touched.add(vid)

    session.commit()
    logger.info(
        "Tag extract: applied=%d videos=%d tags_created=%d",
        applied, len(videos_touched), created,
    )
    return {
        "applied": applied,
        "tags_created": created,
        "tags_reused": len(tag_cache) - created,
        "videos_touched": len(videos_touched),
    }
