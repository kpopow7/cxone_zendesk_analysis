from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from orchestration.analysis.timeframes import TimeWindow
from orchestration.config import Settings, get_settings
from orchestration.db.analytics_views import ensure_analytics_views
from orchestration.db.session import get_engine
from orchestration.rag.index import IndexBuildResult, build_knowledge_index
from orchestration.steps.build_combined_dataset import (
    CombinedDatasetResult,
    run_build_combined_dataset,
)
from orchestration.steps.cxone_transcripts import (
    ExtractionResult as CxoneExtractionResult,
    run_cxone_transcript_extraction,
)
from orchestration.steps.transcript_summary import (
    TranscriptSummaryResult,
    run_transcript_summary_step,
)
from orchestration.steps.zendesk_tickets import (
    ZendeskExtractionResult,
    run_zendesk_ticket_extraction,
)

logger = logging.getLogger(__name__)

# Daily classification/index runs are batched to bound memory on busy days.
_DAILY_CLASSIFY_BATCH_SIZE = 50
_DAILY_CLASSIFY_CHUNK_DAYS = 1
_DAILY_INDEX_BATCH_SIZE = 32


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
    zendesk: ZendeskExtractionResult | None
    combined: CombinedDatasetResult | None
    classification: TranscriptSummaryResult | None
    knowledge_index: IndexBuildResult | None
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


def window_to_time_window(window: DailyWindow) -> TimeWindow:
    """Adapt the daily calendar window to the analysis TimeWindow (CXone interaction bounds)."""
    return TimeWindow(
        preset=None,
        start=window.cxone_start,
        end=window.cxone_end,
        label=f"daily {window.label}",
    )


def run_daily_classification(
    settings: Settings,
    window: DailyWindow,
) -> TranscriptSummaryResult:
    """Classify the day's transcripts and persist the ranked reduction report.

    Uses the batched, full-report path so cxone_transcript_analysis is populated AND the
    ranked reasons + recommendations land in analytics_reduction_recommendations.
    """
    return run_transcript_summary_step(
        settings,
        time_window=window_to_time_window(window),
        batch_size=_DAILY_CLASSIFY_BATCH_SIZE,
        chunk_days=_DAILY_CLASSIFY_CHUNK_DAYS,
        classify_only=False,
    )


def run_daily_knowledge_index(
    settings: Settings,
    window: DailyWindow,
    *,
    database_url: str | None = None,
) -> IndexBuildResult:
    """(Re)embed the day's interactions for chatbot RAG on the given database (default: pipeline DB)."""
    engine = get_engine(database_url or settings.database_url)
    ensure_analytics_views(engine)
    return build_knowledge_index(
        engine,
        api_key=settings.openai_api_key,
        embedding_model=settings.openai_embedding_model,
        openai_base_url=settings.openai_base_url,
        start=window.cxone_start,
        end=window.cxone_end,
        batch_size=_DAILY_INDEX_BATCH_SIZE,
        timeout_seconds=settings.request_timeout_seconds,
        on_progress=logger.info,
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
    skip_classification: bool = False,
    skip_knowledge_index: bool = False,
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

    zendesk_result: ZendeskExtractionResult | None = None
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

    # Root-cause layer: classify transcripts (reasons + reduction report) then refresh RAG.
    # Without these in the daily run, coverage drifts and the chatbot answers go stale.
    classification_result: TranscriptSummaryResult | None = None
    if skip_classification:
        skipped.append("classification")
    elif dry_run:
        skipped.append("classification (dry-run)")
    elif not settings.openai_api_key:
        logger.warning("Skipping classification: OPENAI_API_KEY is not set.")
        skipped.append("classification (no OPENAI_API_KEY)")
    else:
        classification_result = run_daily_classification(settings, window)

    knowledge_index_result: IndexBuildResult | None = None
    if skip_knowledge_index:
        skipped.append("knowledge_index")
    elif dry_run:
        skipped.append("knowledge_index (dry-run)")
    elif not settings.openai_api_key:
        logger.warning("Skipping knowledge index: OPENAI_API_KEY is not set.")
        skipped.append("knowledge_index (no OPENAI_API_KEY)")
    else:
        knowledge_index_result = run_daily_knowledge_index(settings, window)

    return DailyPipelineResult(
        window=window,
        cxone=cxone_result,
        zendesk=zendesk_result,
        combined=combined_result,
        classification=classification_result,
        knowledge_index=knowledge_index_result,
        skipped_steps=skipped,
    )


def _step_ran(step: str, skipped_steps: list[str]) -> bool:
    """A step ran if it isn't skipped (exact match or a "step (reason)" annotation)."""
    return not any(s == step or s.startswith(f"{step} ") for s in skipped_steps)


def railway_sync_tables(skipped_steps: list[str]) -> list[str]:
    """Relational tables to push after a daily run, based on which steps executed."""
    tables: list[str] = []
    if _step_ran("cxone", skipped_steps):
        tables.append("cxone_transcripts")
    if _step_ran("zendesk", skipped_steps):
        tables.append("zendesk_tickets")
    if _step_ran("combined", skipped_steps):
        tables.append("combined_interactions")
    return tables


def railway_classification_sync_tables(skipped_steps: list[str]) -> list[str]:
    """Analysis/reduction tables to push after the classification step ran.

    These have no interaction_start column, so they are synced in a separate pass
    scoped by updated_at/created_at (see railway_classification_sync_args).
    """
    if not _step_ran("classification", skipped_steps):
        return []
    return [
        "cxone_transcript_analysis",
        "transcript_reduction_reports",
        "transcript_reduction_report_reasons",
    ]


def railway_classification_sync_args(window: DailyWindow, tables: list[str]) -> list[str]:
    """argv for sync_to_railway.py to push freshly classified rows (scoped by --since)."""
    return [
        "--tables",
        ",".join(tables),
        "--since",
        window.cxone_start.isoformat(),
    ]


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
