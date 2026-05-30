from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from orchestration.config import Settings, get_settings
from orchestration.steps.build_combined_dataset import (
    CombinedDatasetResult,
    run_build_combined_dataset,
)
from orchestration.steps.cxone_transcripts import (
    ExtractionResult as CxoneExtractionResult,
    run_cxone_transcript_extraction,
)
from orchestration.steps.zendesk_tickets import (
    ZendeskExtractionResult,
    run_zendesk_ticket_extraction,
)


@dataclass
class DailyWindow:
    label: str
    cxone_start: datetime
    cxone_end: datetime
    zendesk_start: datetime
    zendesk_end: datetime
    combined_start: datetime
    combined_end: datetime


@dataclass
class DailyPipelineResult:
    window: DailyWindow
    cxone: CxoneExtractionResult | None
    zendesk: ZendeskTicketExtractionResult | None
    combined: CombinedDatasetResult | None
    skipped_steps: list[str]


def resolve_daily_window(
    *,
    target_date: date | None = None,
    tz_name: str = "UTC",
    zendesk_lookback_days: int = 0,
) -> DailyWindow:
    """Calendar day window in the given timezone (default: yesterday UTC)."""
    tz = ZoneInfo(tz_name)
    if target_date is None:
        target_date = (datetime.now(tz) - timedelta(days=1)).date()

    day_start = datetime.combine(target_date, time.min, tzinfo=tz)
    day_end = datetime.combine(target_date, time.max, tzinfo=tz).replace(microsecond=999999)

    zendesk_start = day_start - timedelta(days=zendesk_lookback_days)

    return DailyWindow(
        label=f"{target_date.isoformat()} ({tz_name})",
        cxone_start=day_start,
        cxone_end=day_end,
        zendesk_start=zendesk_start,
        zendesk_end=day_end,
        combined_start=day_start,
        combined_end=day_end,
    )


def run_daily_pipeline(
    *,
    settings: Settings | None = None,
    target_date: date | None = None,
    tz_name: str = "UTC",
    zendesk_lookback_days: int = 0,
    skip_cxone: bool = False,
    skip_zendesk: bool = False,
    skip_combined: bool = False,
    dry_run: bool = False,
) -> DailyPipelineResult:
    settings = settings or get_settings()
    window = resolve_daily_window(
        target_date=target_date,
        tz_name=tz_name,
        zendesk_lookback_days=zendesk_lookback_days,
    )
    skipped: list[str] = []

    cxone_result: CxoneExtractionResult | None = None
    if skip_cxone:
        skipped.append("cxone")
    else:
        cxone_result = run_cxone_transcript_extraction(
            window.cxone_start,
            window.cxone_end,
            dry_run=dry_run,
            enrich_transcripts=False,
        )

    zendesk_result: ZendeskTicketExtractionResult | None = None
    if skip_zendesk:
        skipped.append("zendesk")
    else:
        zendesk_result = run_zendesk_ticket_extraction(
            window.zendesk_start,
            window.zendesk_end,
            dry_run=dry_run,
        )

    combined_result: CombinedDatasetResult | None = None
    if skip_combined:
        skipped.append("combined")
    else:
        combined_result = run_build_combined_dataset(
            settings=settings,
            interaction_start=window.combined_start,
            interaction_end=window.combined_end,
            rebuild=False,
            dry_run=dry_run,
        )

    return DailyPipelineResult(
        window=window,
        cxone=cxone_result,
        zendesk=zendesk_result,
        combined=combined_result,
        skipped_steps=skipped,
    )
