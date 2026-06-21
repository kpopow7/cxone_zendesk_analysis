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


def railway_sync_tables(skipped_steps: list[str]) -> list[str]:
    """Tables to push after a daily run, based on which steps executed."""
    tables: list[str] = []
    if "cxone" not in skipped_steps:
        tables.append("cxone_transcripts")
    if "zendesk" not in skipped_steps:
        tables.append("zendesk_tickets")
    if "combined" not in skipped_steps:
        tables.append("combined_interactions")
    return tables


@dataclass(frozen=True)
class RailwaySyncFilter:
    """Business-date filters for incremental Railway sync after a daily run."""

    interaction_start: datetime | None
    interaction_end: datetime | None
    ticket_created_start: datetime | None
    ticket_created_end: datetime | None
    include_linked_tickets: bool


def railway_sync_filter(window: DailyWindow, skipped_steps: list[str]) -> RailwaySyncFilter | None:
    """Scope sync to the target calendar day (not Zendesk lookback overlap)."""
    if not railway_sync_tables(skipped_steps):
        return None

    interaction_start: datetime | None = None
    interaction_end: datetime | None = None
    if "cxone" not in skipped_steps or "combined" not in skipped_steps:
        interaction_start = window.cxone_start
        interaction_end = window.cxone_end

    ticket_created_start: datetime | None = None
    ticket_created_end: datetime | None = None
    if "zendesk" not in skipped_steps:
        ticket_created_start = window.cxone_start
        ticket_created_end = window.cxone_end

    include_linked_tickets = (
        "zendesk" not in skipped_steps and "combined" not in skipped_steps
    )

    return RailwaySyncFilter(
        interaction_start=interaction_start,
        interaction_end=interaction_end,
        ticket_created_start=ticket_created_start,
        ticket_created_end=ticket_created_end,
        include_linked_tickets=include_linked_tickets,
    )


def railway_sync_cli_args(sync_filter: RailwaySyncFilter, tables: list[str]) -> list[str]:
    """Build argv for scripts/sync_to_railway.py from a daily-run filter."""
    args = ["--tables", ",".join(tables)]
    if sync_filter.interaction_start is not None and sync_filter.interaction_end is not None:
        args.extend(["--interaction-start", sync_filter.interaction_start.isoformat()])
        args.extend(["--interaction-end", sync_filter.interaction_end.isoformat()])
    if sync_filter.ticket_created_start is not None and sync_filter.ticket_created_end is not None:
        args.extend(["--ticket-created-start", sync_filter.ticket_created_start.isoformat()])
        args.extend(["--ticket-created-end", sync_filter.ticket_created_end.isoformat()])
    if sync_filter.include_linked_tickets:
        args.append("--include-linked-tickets")
    return args
