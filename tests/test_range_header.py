"""HTTP Range header parser — edge cases matter, browsers are creative."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.routers.streaming import parse_range_header


def test_full_range():
    assert parse_range_header("bytes=0-99", 100) == (0, 99)


def test_open_ended_range():
    assert parse_range_header("bytes=0-", 100) == (0, 99)


def test_suffix_range():
    # "last 10 bytes of a 100-byte file" → offsets 90..99
    assert parse_range_header("bytes=-10", 100) == (90, 99)


def test_clamps_end_past_eof():
    assert parse_range_header("bytes=0-999", 100) == (0, 99)


def test_rejects_wrong_unit():
    with pytest.raises(HTTPException) as ei:
        parse_range_header("items=0-9", 100)
    assert ei.value.status_code == 416


def test_rejects_start_past_eof():
    with pytest.raises(HTTPException):
        parse_range_header("bytes=200-", 100)


def test_rejects_negative_start():
    with pytest.raises(HTTPException):
        parse_range_header("bytes=-0", 100)


def test_rejects_malformed():
    with pytest.raises(HTTPException):
        parse_range_header("bytes=abc", 100)
