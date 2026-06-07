from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from orchestration.analysis.transcript_summary_config import (
    DEFAULT_TRANSCRIPT_SUMMARY_CONFIG,
)
from orchestration.analysis.transcript_summary_report import _filter_transcript_rows


def _row(
    segment_id: str,
    *,
    transcript_text: str = "hello",
    call_direction: str = "IN_BOUND",
    media_type: str = "PhoneCall",
) -> SimpleNamespace:
    return SimpleNamespace(
        segment_id=segment_id,
        transcript_text=transcript_text,
        call_direction=call_direction,
        media_type=media_type,
        skill_name="Test Skill",
        team_name=None,
    )


def test_filter_transcript_rows_respects_sample_limit() -> None:
    config = replace(
        DEFAULT_TRANSCRIPT_SUMMARY_CONFIG,
        classification=replace(
            DEFAULT_TRANSCRIPT_SUMMARY_CONFIG.classification,
            sample_limit=2,
        ),
    )
    rows = [_row("a"), _row("b"), _row("c")]

    filtered, remaining = _filter_transcript_rows(rows, config, sample_remaining=2)

    assert [row.segment_id for row in filtered] == ["a", "b"]
    assert remaining == 0


def test_filter_transcript_rows_skips_empty_transcripts() -> None:
    config = DEFAULT_TRANSCRIPT_SUMMARY_CONFIG
    rows = [_row("a"), _row("b", transcript_text="   ")]

    filtered, remaining = _filter_transcript_rows(rows, config, sample_remaining=None)

    assert [row.segment_id for row in filtered] == ["a"]
    assert remaining is None
