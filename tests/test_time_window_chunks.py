from __future__ import annotations

from datetime import datetime, timezone

from orchestration.analysis.timeframes import TimeWindow, iter_time_window_chunks


def test_iter_time_window_chunks_single_day_steps() -> None:
    window = TimeWindow(
        preset="custom",
        start=datetime(2026, 3, 5, tzinfo=timezone.utc),
        end=datetime(2026, 3, 7, 23, 59, 59, 999999, tzinfo=timezone.utc),
        label="test",
    )

    chunks = iter_time_window_chunks(window, 1)

    assert len(chunks) == 3
    assert chunks[0].start.date().isoformat() == "2026-03-05"
    assert chunks[2].end.date().isoformat() == "2026-03-07"


def test_iter_time_window_chunks_week_blocks() -> None:
    window = TimeWindow(
        preset="custom",
        start=datetime(2026, 3, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 10, 23, 59, 59, 999999, tzinfo=timezone.utc),
        label="test",
    )

    chunks = iter_time_window_chunks(window, 7)

    assert len(chunks) == 2
    assert chunks[0].start.date().isoformat() == "2026-03-01"
    assert chunks[0].end.date().isoformat() == "2026-03-07"
    assert chunks[1].start.date().isoformat() == "2026-03-08"
    assert chunks[1].end.date().isoformat() == "2026-03-10"


def test_iter_time_window_chunks_rejects_invalid_size() -> None:
    window = TimeWindow(
        preset="custom",
        start=datetime(2026, 3, 1, tzinfo=timezone.utc),
        end=datetime(2026, 3, 2, tzinfo=timezone.utc),
        label="test",
    )

    try:
        iter_time_window_chunks(window, 0)
    except ValueError as exc:
        assert "chunk_days" in str(exc)
    else:
        raise AssertionError("expected ValueError")
