from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum


class TimeFramePreset(str, Enum):
    ALL = "all"
    YESTERDAY = "yesterday"
    LAST_WEEK = "last-week"
    LAST_7_DAYS = "last-7-days"


@dataclass(frozen=True)
class TimeWindow:
    preset: str | None
    start: datetime | None
    end: datetime | None
    label: str

    @property
    def is_unbounded(self) -> bool:
        return self.start is None and self.end is None


def _utc_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = datetime.combine(day, time.max, tzinfo=timezone.utc).replace(microsecond=999999)
    return start, end


def _previous_calendar_week(reference: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00 UTC through Sunday 23:59:59.999 UTC of the week before reference's week."""
    ref_date = reference.date()
    days_since_monday = ref_date.weekday()
    this_monday = ref_date - timedelta(days=days_since_monday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)
    return _utc_day_bounds(last_monday)[0], _utc_day_bounds(last_sunday)[1]


def resolve_time_window(
    *,
    preset: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
) -> TimeWindow:
    """Resolve analysis window from preset and/or explicit ISO bounds."""
    if start is not None or end is not None:
        if start and end and end <= start:
            raise ValueError("end must be after start")
        label_parts: list[str] = []
        if preset:
            label_parts.append(preset)
        if start:
            label_parts.append(f"from {start.isoformat()}")
        if end:
            label_parts.append(f"to {end.isoformat()}")
        return TimeWindow(
            preset=preset,
            start=start,
            end=end,
            label=" ".join(label_parts) if label_parts else "custom range",
        )

    if not preset or preset in (TimeFramePreset.ALL.value, "all"):
        return TimeWindow(preset=TimeFramePreset.ALL.value, start=None, end=None, label="all time")

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    normalized = preset.strip().lower().replace("_", "-")

    if normalized in (TimeFramePreset.YESTERDAY.value, "yesterday"):
        yesterday = (reference.date() - timedelta(days=1))
        window_start, window_end = _utc_day_bounds(yesterday)
        return TimeWindow(
            preset=TimeFramePreset.YESTERDAY.value,
            start=window_start,
            end=window_end,
            label=f"yesterday ({yesterday.isoformat()} UTC)",
        )

    if normalized in (TimeFramePreset.LAST_WEEK.value, "last-week", "lastweek"):
        window_start, window_end = _previous_calendar_week(reference)
        return TimeWindow(
            preset=TimeFramePreset.LAST_WEEK.value,
            start=window_start,
            end=window_end,
            label=(
                f"last calendar week "
                f"({window_start.date().isoformat()} - {window_end.date().isoformat()} UTC)"
            ),
        )

    if normalized in (TimeFramePreset.LAST_7_DAYS.value, "last-7-days", "last7days"):
        window_end = reference
        window_start = reference - timedelta(days=7)
        return TimeWindow(
            preset=TimeFramePreset.LAST_7_DAYS.value,
            start=window_start,
            end=window_end,
            label="last 7 days (rolling)",
        )

    raise ValueError(
        f"Unknown timeframe preset: {preset!r}. "
        "Use all, yesterday, last-week, last-7-days, or --start/--end."
    )
