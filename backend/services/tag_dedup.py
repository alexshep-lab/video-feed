"""Near-duplicate tag clustering + manual merge.

``tag_normalize`` handles mechanical stripping (count suffixes, ``.com``,
``Pack_scr``). What's left after that are tags the rules *can't* reason
about — typos, transliteration variants, alternate spellings:

    valentina nappi / valentina napi     (typo, edit distance 1)
    valentinanappi  / valentina nappi    (missing space)
    sasha_grey / sasha grey              (underscore artifact the normalizer missed)

This module surfaces those clusters for human review. We intentionally do
NOT auto-merge — close names can also be genuinely different people or
unrelated studios. The user picks a canonical name per cluster and merges.
"""
from __future__ import annotations

import difflib
import logging
import re
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Tag, video_tags
from .tag_normalize import merge_tag_rows

logger = logging.getLogger("videofeed.tag_dedup")


_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def fingerprint(name: str) -> str:
    """Collapse to lowercase alphanumerics — used for safe equivalence.

    ``"Valentina Nappi"``, ``"valentina_nappi"``, ``"valentinanappi"`` all
    map to ``"valentinanappi"``. Safe auto-merge candidates share a
    fingerprint.
    """
    return _ALNUM_RE.sub("", name.lower())


# Below this, short-name false positives dominate (Alice / Alicia / Alice1).
# Above 0.88 we're in "clearly similar" territory.
_FUZZY_CUTOFF = 0.88
_FUZZY_MIN_LEN = 6


def find_tag_clusters(session: Session) -> dict:
    """Group tags by fingerprint + near-fingerprint fuzzy match.

    Returns two kinds of clusters:
      - ``fingerprint`` — identical collapsed form, high confidence. The
        canonical is chosen automatically (longest/most-used variant).
      - ``fuzzy`` — small edit-distance variants, medium confidence. User
        should eyeball each group before merging.
    """
    video_counts: dict[int, int] = {
        row[0]: row[1]
        for row in session.execute(
            select(video_tags.c.tag_id, func.count().label("n"))
            .group_by(video_tags.c.tag_id)
        ).all()
    }
    all_tags = session.scalars(select(Tag)).all()
    if not all_tags:
        return {"fingerprint_clusters": [], "fuzzy_clusters": []}

    # --- Fingerprint buckets ----------------------------------------------
    fp_buckets: dict[str, list[Tag]] = defaultdict(list)
    for tag in all_tags:
        fp_buckets[fingerprint(tag.name)].append(tag)

    fingerprint_clusters: list[dict] = []
    # Tags that already ended up in a multi-entry fingerprint cluster are
    # skipped by the fuzzy pass — they're already handled.
    clustered_ids: set[int] = set()

    for fp, tags in fp_buckets.items():
        if len(tags) < 2:
            continue
        fingerprint_clusters.append(_build_cluster(tags, video_counts))
        for t in tags:
            clustered_ids.add(t.id)

    # --- Fuzzy pass -------------------------------------------------------
    #
    # Compare only tags NOT already in a fingerprint cluster. We also bucket
    # by first character to cut the O(n²) space: a near-miss at edit
    # distance 1 still shares the first char almost always. Finally we use
    # difflib's cheap pre-filters (real_quick_ratio/quick_ratio) via
    # get_close_matches which is fast in practice.
    remaining = [t for t in all_tags if t.id not in clustered_ids and len(t.name) >= _FUZZY_MIN_LEN]
    prefix_buckets: dict[str, list[Tag]] = defaultdict(list)
    for tag in remaining:
        if tag.name:
            prefix_buckets[tag.name[0]].append(tag)

    # Union-find so overlapping pairs (A~B, B~C) collapse into one cluster.
    parent: dict[int, int] = {t.id: t.id for t in remaining}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for bucket in prefix_buckets.values():
        if len(bucket) < 2:
            continue
        names = [t.name for t in bucket]
        by_name = {t.name: t for t in bucket}
        for tag in bucket:
            matches = difflib.get_close_matches(
                tag.name,
                [n for n in names if n != tag.name],
                n=6,
                cutoff=_FUZZY_CUTOFF,
            )
            for m in matches:
                union(tag.id, by_name[m].id)

    fuzzy_buckets: dict[int, list[Tag]] = defaultdict(list)
    for tag in remaining:
        root = find(tag.id)
        fuzzy_buckets[root].append(tag)

    fuzzy_clusters: list[dict] = [
        _build_cluster(tags, video_counts, kind="fuzzy")
        for tags in fuzzy_buckets.values()
        if len(tags) >= 2
    ]

    # Biggest (most videos touched) first — user's attention is best spent there.
    fingerprint_clusters.sort(key=lambda c: -c["total_videos"])
    fuzzy_clusters.sort(key=lambda c: -c["total_videos"])

    return {
        "fingerprint_clusters": fingerprint_clusters,
        "fuzzy_clusters": fuzzy_clusters,
    }


def _build_cluster(tags: list[Tag], video_counts: dict[int, int], kind: str = "fingerprint") -> dict:
    """Serialize a group of equivalent/similar tags for the API response."""
    members = sorted(
        ({"id": t.id, "name": t.name, "videos": video_counts.get(t.id, 0)} for t in tags),
        key=lambda m: -m["videos"],
    )
    # Suggested canonical: most-used member; ties broken by shortest name,
    # then alphabetical. Short names tend to be the "clean" variant.
    canonical = sorted(
        members,
        key=lambda m: (-m["videos"], len(m["name"]), m["name"]),
    )[0]
    return {
        "kind": kind,
        "suggested_canonical": canonical["name"],
        "total_videos": sum(m["videos"] for m in members),
        "members": members,
    }


def merge_tags_manual(
    session: Session,
    canonical_name: str,
    source_names: list[str],
) -> dict:
    """Merge ``source_names`` into ``canonical_name``.

    Creates the canonical tag if it doesn't exist yet (so the user can pick
    a brand-new name if none of the cluster members is clean enough). Sources
    that don't exist are silently skipped. Runs as a single transaction.
    """
    canonical_name = canonical_name.strip().lower()
    if not canonical_name:
        raise ValueError("canonical_name is empty")

    src_names_clean = [s.strip().lower() for s in source_names if s and s.strip()]
    src_names_clean = [s for s in src_names_clean if s != canonical_name]
    if not src_names_clean:
        return {"merged": 0, "links_remapped": 0, "canonical": canonical_name, "created": False}

    canonical = session.scalar(select(Tag).where(Tag.name == canonical_name))
    created = False
    if canonical is None:
        canonical = Tag(name=canonical_name)
        session.add(canonical)
        session.flush()
        created = True

    merged = 0
    links = 0
    for name in src_names_clean:
        src = session.scalar(select(Tag).where(Tag.name == name))
        if src is None or src.id == canonical.id:
            continue
        links += merge_tag_rows(session, src, canonical)
        merged += 1

    session.commit()
    logger.info(
        "Manual tag merge: canonical=%r sources=%r merged=%d links=%d created=%s",
        canonical_name, src_names_clean, merged, links, created,
    )
    return {
        "merged": merged,
        "links_remapped": links,
        "canonical": canonical_name,
        "created": created,
    }
