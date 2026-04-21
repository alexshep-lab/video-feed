"""Pure-function tests for tag name normalization."""
from __future__ import annotations

import pytest

from backend.services.tag_normalize import normalize_tag_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Identity / trivial cases
        ("alice", "alice"),
        ("Alice", "alice"),
        ("  Alice  ", "alice"),
        # Underscore → space
        ("Alice_Smith", "alice smith"),
        # Count suffix
        ("Alice (66)", "alice"),
        ("Alice (2022)", "alice"),
        # Standalone trailing number
        ("Alice 66", "alice"),
        # Site suffix
        ("GuysForMatures.com", "guysformatures"),
        ("Foo.net", "foo"),
        # Bracket prefix
        ("[PornHubPremium.com] Alice", "alice"),
        # Screen-pack trailing
        ("Sophie_Lynx_scr", "sophie lynx"),
        ("Adria Rae Pack scr", "adria rae"),
        ("Ash_Hollywood_HEVC_Pack_scr", "ash hollywood"),
        # Service folders → rejected
        ("screens", None),
        ("Screenshots", None),
        ("incoming", None),
        ("Screens_all", None),  # "screens all" after normalization
        ("squized", None),
        ("converted", None),
        # Too short / digits only
        ("a", None),
        ("42", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_tag_name(raw, expected):
    assert normalize_tag_name(raw) == expected
