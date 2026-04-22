"""Filename-based tag extraction — covers the different naming patterns
that show up in real libraries (underscores, spaces, dashes, brackets)."""
from __future__ import annotations

import pytest

from backend.services.tag_extract import extract_tags_from_filename


@pytest.mark.parametrize(
    ("filename", "folder_hint", "expected_subset"),
    [
        # Underscore studio prefix + &-joined actors (no spaces)
        (
            "stunningmatures_g603_Emilia&Arthur.mp4",
            None,
            {"stunningmatures", "emilia", "arthur"},
        ),
        # stm short-form with folder hint expanding to full studio name
        (
            "stm_g536_Carol&Adam.mp4",
            "StunningMatures",
            {"stunningmatures", "carol", "adam"},
        ),
        # Space-separated prefix + gNNN scene code + spaces around "&"
        (
            "girlsformatures g1009 Susanna & Nora.wmv",
            "Susanna",
            {"girlsformatures", "susanna", "nora"},
        ),
        (
            "straponscreen g740 Susanna & Connor.mp4",
            None,
            {"straponscreen", "susanna", "connor"},
        ),
        # Dash-separated CamelCase studio, with leading "!"
        (
            "!DorcelClub - 2020.03.02 Anissa Kate, Poppy Pleasure - The Perfect Hosts 2160p FHD.mp4",
            None,
            {"dorcelclub", "4k"},  # 2160p → 4k
        ),
        # Dash-separated without "!" prefix
        (
            "FemdomEmpire - 2014 Fucked Cum Lapper 720p.mp4",
            None,
            {"femdomempire", "720p"},
        ),
        # Digit-leading CamelCase studio with dash
        (
            "21FootArt - 2015.03.21 Princess 1080p.mp4",
            None,
            {"21footart", "1080p"},
        ),
        # Bracketed CamelCase studio prefix
        (
            "[3rdDegree] Girlfriends 6 (2013) sc 4 Ash and Katie St. Ives (480p).mkv",
            None,
            {"3rddegree", "480p"},
        ),
        (
            "[AddictedToGirls] How To Kiss A Girl 2 (2013) sc 2 (480p).mkv",
            None,
            {"addictedtogirls", "480p"},
        ),
        # Negative: plain "Mila - foo" should NOT produce "mila" as a studio
        # (no internal uppercase → fails the mixedCase constraint).
        (
            "Mila - vacation clip.mp4",
            None,
            set(),
        ),
        # Negative: generic sentence with spaces but no scene-code anchor
        (
            "Summer vacation 2019.mp4",
            None,
            set(),
        ),
        # Generic filename with no recognizable structure
        (
            "random_file_12345.mp4",
            None,
            set(),
        ),
    ],
)
def test_extract_tags_from_filename(filename, folder_hint, expected_subset):
    out = extract_tags_from_filename(filename, folder_hint=folder_hint)
    assert expected_subset.issubset(out), (
        f"For {filename!r} expected at least {expected_subset}, got {out}"
    )
