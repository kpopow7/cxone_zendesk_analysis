from __future__ import annotations

from datetime import date, datetime, timezone

from orchestration.analysis.sync_audit import (
    AUDIT_SPECS,
    DEFAULT_AUDIT_TABLES,
    TableSnapshot,
    all_in_sync,
    compare_snapshots,
)

SPEC = AUDIT_SPECS["combined_interactions"]


def _snapshot(total: int, daily: dict[date, int], **kw) -> TableSnapshot:
    return TableSnapshot(
        table=SPEC.name,
        exists=True,
        total=total,
        daily_counts=daily,
        **kw,
    )


def test_identical_snapshots_are_ok() -> None:
    d = date(2026, 6, 22)
    ts = datetime(2026, 6, 22, 12, tzinfo=timezone.utc)
    src = _snapshot(3, {d: 3}, min_date=ts, max_date=ts, max_updated=ts)
    tgt = _snapshot(3, {d: 3}, min_date=ts, max_date=ts, max_updated=ts)

    cmp = compare_snapshots(SPEC, src, tgt)

    assert cmp.status == "OK"
    assert cmp.count_delta == 0
    assert cmp.daily_deltas == ()
    assert cmp.date_range_matches
    assert all_in_sync([cmp])


def test_target_missing_rows_for_a_day() -> None:
    d1, d2 = date(2026, 6, 21), date(2026, 6, 22)
    src = _snapshot(5, {d1: 2, d2: 3})
    tgt = _snapshot(2, {d1: 2})  # day 2 never synced

    cmp = compare_snapshots(SPEC, src, tgt)

    assert cmp.status == "MISMATCH"
    assert cmp.count_delta == -3
    assert cmp.missing_in_target_days == 1
    assert cmp.extra_in_target_days == 0
    assert [d.day for d in cmp.daily_deltas] == [d2]
    assert cmp.daily_deltas[0].delta == -3
    assert not all_in_sync([cmp])


def test_extra_rows_in_target() -> None:
    d = date(2026, 6, 22)
    src = _snapshot(2, {d: 2})
    tgt = _snapshot(4, {d: 4})

    cmp = compare_snapshots(SPEC, src, tgt)

    assert cmp.status == "MISMATCH"
    assert cmp.extra_in_target_days == 1
    assert cmp.daily_deltas[0].delta == 2


def test_date_range_mismatch_flags_even_when_counts_match() -> None:
    d = date(2026, 6, 22)
    early = datetime(2026, 6, 1, tzinfo=timezone.utc)
    late = datetime(2026, 6, 22, tzinfo=timezone.utc)
    src = _snapshot(3, {d: 3}, min_date=early, max_date=late)
    tgt = _snapshot(3, {d: 3}, min_date=late, max_date=late)

    cmp = compare_snapshots(SPEC, src, tgt)

    assert not cmp.date_range_matches
    assert cmp.status == "MISMATCH"


def test_missing_table_status() -> None:
    src = _snapshot(3, {date(2026, 6, 22): 3})
    tgt = TableSnapshot(table=SPEC.name, exists=False)

    cmp = compare_snapshots(SPEC, src, tgt)

    assert cmp.status == "MISSING_TABLE"


def test_error_status_takes_precedence() -> None:
    src = _snapshot(3, {date(2026, 6, 22): 3})
    tgt = TableSnapshot(table=SPEC.name, exists=True, error="connection refused")

    cmp = compare_snapshots(SPEC, src, tgt)

    assert cmp.status == "ERROR"


def test_default_tables_have_specs() -> None:
    for name in DEFAULT_AUDIT_TABLES:
        assert name in AUDIT_SPECS
