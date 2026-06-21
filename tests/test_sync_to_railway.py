from __future__ import annotations

from datetime import datetime, timezone

from scripts.sync_to_railway import (
    TABLE_SYNC_TIMESTAMP_COLUMN,
    SyncRowFilter,
    _build_keyset_where,
)


def test_build_keyset_where_since_only() -> None:
    since = datetime(2026, 6, 10, tzinfo=timezone.utc)

    clause, params = _build_keyset_where(
        table_name="cxone_transcripts",
        pk_name="segment_id",
        updated_column="updated_at",
        row_filter=None,
        last_pk=None,
    )
    assert clause == ""
    assert params == {}

    clause, params = _build_keyset_where(
        table_name="cxone_transcripts",
        pk_name="segment_id",
        updated_column="updated_at",
        row_filter=SyncRowFilter(since=since),
        last_pk=None,
    )

    assert clause == "WHERE updated_at >= :since"
    assert params == {"since": since}


def test_build_keyset_where_interaction_window() -> None:
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 10, 23, 59, 59, 999999, tzinfo=timezone.utc)

    clause, params = _build_keyset_where(
        table_name="combined_interactions",
        pk_name="segment_id",
        updated_column="updated_at",
        row_filter=SyncRowFilter(interaction_start=start, interaction_end=end),
        last_pk=None,
    )

    assert "interaction_start >= :interaction_start" in clause
    assert "interaction_start <= :interaction_end" in clause
    assert params["interaction_start"] == start
    assert params["interaction_end"] == end


def test_build_keyset_where_zendesk_created_or_linked() -> None:
    start = datetime(2026, 6, 10, tzinfo=timezone.utc)
    end = datetime(2026, 6, 10, 23, 59, 59, 999999, tzinfo=timezone.utc)

    clause, params = _build_keyset_where(
        table_name="zendesk_tickets",
        pk_name="ticket_id",
        updated_column="row_updated_at",
        row_filter=SyncRowFilter(
            ticket_created_start=start,
            ticket_created_end=end,
            linked_ticket_ids=(101, 202),
        ),
        last_pk=None,
    )

    assert "created_at >= :ticket_created_start" in clause
    assert "ticket_id = ANY(:linked_ticket_ids)" in clause
    assert params["linked_ticket_ids"] == [101, 202]


def test_sync_timestamp_columns_cover_all_tables() -> None:
    expected = {
        "cxone_transcripts": "updated_at",
        "cxone_transcript_analysis": "updated_at",
        "zendesk_tickets": "row_updated_at",
        "combined_interactions": "updated_at",
    }
    assert TABLE_SYNC_TIMESTAMP_COLUMN == expected
