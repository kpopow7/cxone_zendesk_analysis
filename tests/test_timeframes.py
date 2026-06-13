from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestration.analysis.timeframes import parse_window_bound, resolve_time_window


def test_resolve_time_window_presets() -> None:
    reference = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)

    all_time = resolve_time_window(preset="all", now=reference)
    assert all_time.start is None
    assert all_time.end is None

    yesterday = resolve_time_window(preset="yesterday", now=reference)
    assert yesterday.start == datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
    assert yesterday.end == datetime(2026, 6, 11, 23, 59, 59, 999999, tzinfo=timezone.utc)

    last_week = resolve_time_window(preset="last-week", now=reference)
    assert last_week.start == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    assert last_week.end == datetime(2026, 6, 7, 23, 59, 59, 999999, tzinfo=timezone.utc)

    rolling = resolve_time_window(preset="last-7-days", now=reference)
    assert rolling.start == datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    assert rolling.end == reference


def test_resolve_time_window_custom_range() -> None:
    start = datetime(2026, 3, 5, tzinfo=timezone.utc)
    end = datetime(2026, 3, 11, 23, 59, 59, tzinfo=timezone.utc)

    window = resolve_time_window(start=start, end=end)

    assert window.start == start
    assert window.end == end
    assert "2026-03-05" in window.label
    assert "2026-03-11" in window.label


def test_resolve_time_window_rejects_invalid_range() -> None:
    start = datetime(2026, 3, 11, tzinfo=timezone.utc)
    end = datetime(2026, 3, 5, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="end must be after start"):
        resolve_time_window(start=start, end=end)


def test_parse_window_bound_expands_date_only_values() -> None:
    start = parse_window_bound("2026-03-05", is_end=False)
    end = parse_window_bound("2026-03-11", is_end=True)

    assert start == datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 3, 11, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_parse_window_bound_accepts_iso_datetimes() -> None:
    parsed = parse_window_bound("2026-03-05T15:30:00Z", is_end=False)

    assert parsed == datetime(2026, 3, 5, 15, 30, tzinfo=timezone.utc)
