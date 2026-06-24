from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from orchestration.steps.daily_pipeline import (
    railway_classification_sync_args,
    railway_classification_sync_tables,
    railway_sync_cli_args,
    railway_sync_filter,
    railway_sync_tables,
    resolve_daily_window,
)


def _window_for(target_date: date) -> object:
    return resolve_daily_window(
        target_date=target_date,
        tz_name="UTC",
        zendesk_lookback_days=2,
    )


def test_railway_sync_tables_all_steps() -> None:
    assert railway_sync_tables([]) == [
        "cxone_transcripts",
        "zendesk_tickets",
        "combined_interactions",
    ]


def test_railway_sync_tables_respects_skipped_steps() -> None:
    assert railway_sync_tables(["zendesk"]) == [
        "cxone_transcripts",
        "combined_interactions",
    ]
    assert railway_sync_tables(["cxone", "zendesk", "combined"]) == []


def test_railway_classification_sync_tables_when_classification_ran() -> None:
    assert railway_classification_sync_tables([]) == [
        "cxone_transcript_analysis",
        "transcript_reduction_reports",
        "transcript_reduction_report_reasons",
    ]


def test_railway_classification_sync_tables_empty_when_skipped() -> None:
    assert railway_classification_sync_tables(["classification"]) == []
    # Skips can carry a reason annotation, e.g. "classification (no OPENAI_API_KEY)".
    assert railway_classification_sync_tables(["classification (dry-run)"]) == []


def test_railway_classification_sync_args_scopes_by_since() -> None:
    window = _window_for(date(2026, 6, 10))
    tables = railway_classification_sync_tables([])

    args = railway_classification_sync_args(window, tables)

    assert args[0] == "--tables"
    assert "cxone_transcript_analysis" in args[1]
    assert "--since" in args
    since_value = args[args.index("--since") + 1]
    assert since_value == window.cxone_start.isoformat()


def test_railway_sync_filter_uses_target_day_not_zendesk_lookback() -> None:
    window = _window_for(date(2026, 6, 10))

    sync_filter = railway_sync_filter(window, [])

    assert sync_filter is not None
    assert sync_filter.interaction_start == window.cxone_start
    assert sync_filter.interaction_end == window.cxone_end
    assert sync_filter.ticket_created_start == window.cxone_start
    assert sync_filter.ticket_created_end == window.cxone_end
    assert sync_filter.ticket_created_start > window.zendesk_start
    assert sync_filter.include_linked_tickets is True


def test_railway_sync_filter_when_zendesk_skipped() -> None:
    window = _window_for(date(2026, 6, 10))

    sync_filter = railway_sync_filter(window, ["zendesk"])

    assert sync_filter is not None
    assert sync_filter.ticket_created_start is None
    assert sync_filter.include_linked_tickets is False


def test_railway_sync_filter_none_when_all_skipped() -> None:
    window = _window_for(date(2026, 6, 10))

    assert railway_sync_filter(window, ["cxone", "zendesk", "combined"]) is None


def test_railway_sync_cli_args_includes_linked_tickets_flag() -> None:
    window = resolve_daily_window(
        target_date=date(2026, 6, 10),
        tz_name="America/New_York",
        zendesk_lookback_days=2,
    )
    sync_filter = railway_sync_filter(window, [])
    assert sync_filter is not None

    args = railway_sync_cli_args(
        sync_filter,
        ["cxone_transcripts", "zendesk_tickets", "combined_interactions"],
    )

    assert "--interaction-start" in args
    assert "--ticket-created-start" in args
    assert "--include-linked-tickets" in args
    assert "--since" not in args
    assert sync_filter.interaction_start is not None
    assert sync_filter.interaction_start.tzinfo == ZoneInfo("America/New_York")
