from __future__ import annotations

from datetime import datetime, timezone

from orchestration.analysis.transcript_summary_report import (
    PrimaryReasonBucket,
    SecondaryBreakdownItem,
    TertiaryBreakdownItem,
    TranscriptSummaryReport,
    build_reduction_report_records,
)


def _report_with_buckets() -> TranscriptSummaryReport:
    bucket = PrimaryReasonBucket(
        primary_reason="Order status",
        primary_key="order_status",
        count=42,
        share_pct=18.5,
        importance_score=73.2,
        negative_sentiment_pct=12.0,
        secondary=[
            SecondaryBreakdownItem(
                secondary_reason="Where is my order",
                count=30,
                share_of_primary_pct=71.4,
                tertiary=[
                    TertiaryBreakdownItem(
                        tertiary_reason="Tracking number request",
                        count=10,
                        share_of_secondary_pct=33.3,
                    )
                ],
            )
        ],
        sample_summaries=["Customer asked about shipping"],
        sample_segment_ids=["seg-1", "seg-2"],
        reduction_hints=["Send proactive shipping updates"],
        recommendations=["Add tracking SMS", "Surface ETA in IVR"],
        recommendation_source="llm",
    )
    return TranscriptSummaryReport(
        generated_at="2026-06-23T15:00:00+00:00",
        timeframe={
            "preset": "last-week",
            "start": "2026-06-09T00:00:00+00:00",
            "end": "2026-06-15T23:59:59+00:00",
            "label": "last week",
        },
        filters={"call_direction": "inbound"},
        totals={"transcripts_analyzed": 227},
        classification={"concurrency": 4},
        top_primary_reasons=[bucket],
        insights=["Top reason: Order status"],
        llm={"reduction_llm_applied": True},
    )


def test_build_records_maps_header_fields() -> None:
    records = build_reduction_report_records(_report_with_buckets())

    header = records.header
    assert header["generated_at"] == datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
    assert header["timeframe_preset"] == "last-week"
    assert header["timeframe_label"] == "last week"
    assert header["timeframe_start"] == datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc)
    assert header["transcripts_analyzed"] == 227
    assert header["reason_count"] == 1
    assert header["filters"] == {"call_direction": "inbound"}


def test_build_records_maps_ranked_reasons_and_recommendations() -> None:
    records = build_reduction_report_records(_report_with_buckets())

    assert len(records.reasons) == 1
    reason = records.reasons[0]
    assert reason["rank"] == 1
    assert reason["primary_reason"] == "Order status"
    assert reason["primary_reason_key"] == "order_status"
    assert reason["call_count"] == 42
    assert reason["share_pct"] == 18.5
    assert reason["recommendation_source"] == "llm"
    assert reason["recommendations"] == ["Add tracking SMS", "Surface ETA in IVR"]
    assert reason["recommendations_text"] == "Add tracking SMS\nSurface ETA in IVR"
    assert reason["reduction_hints"] == ["Send proactive shipping updates"]
    # Secondary breakdown is serialized to plain dicts (JSONB-ready), nested tertiary kept.
    assert reason["secondary"][0]["secondary_reason"] == "Where is my order"
    assert reason["secondary"][0]["tertiary"][0]["tertiary_reason"] == "Tracking number request"
    assert reason["sample_segment_ids"] == ["seg-1", "seg-2"]


def test_build_records_empty_when_no_buckets() -> None:
    report = _report_with_buckets()
    report.top_primary_reasons = []

    records = build_reduction_report_records(report)

    assert records.reasons == []
    assert records.header["reason_count"] == 0
