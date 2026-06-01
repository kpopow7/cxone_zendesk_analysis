from __future__ import annotations

from orchestration.analysis.transcript_reason_llm import (
    TranscriptClassificationError,
    parse_transcript_reason_analysis,
    reason_keys,
)


def test_parse_transcript_reason_analysis_valid() -> None:
    content = """
    {
      "transcript_summary": "Caller asked to place a remake for order 12345.",
      "primary_reason": "Remake order",
      "secondary_reason": "Place new remake order",
      "tertiary_reason": "Agent entered remake in system",
      "reduction_hint": "Offer self-service remake when order is eligible."
    }
    """
    result = parse_transcript_reason_analysis(content)
    assert "remake" in result.transcript_summary.lower()
    assert result.primary_reason == "Remake order"
    assert result.secondary_reason == "Place new remake order"
    assert result.tertiary_reason == "Agent entered remake in system"
    assert result.reduction_hint is not None

    primary_key, secondary_key, tertiary_key = reason_keys(result)
    assert primary_key == "remake order"
    assert secondary_key == "place new remake order"
    assert tertiary_key == "agent entered remake in system"


def test_parse_transcript_reason_analysis_null_tertiary() -> None:
    content = """
    {
      "transcript_summary": "Short billing question resolved.",
      "primary_reason": "Billing",
      "secondary_reason": "Charge explanation",
      "tertiary_reason": null,
      "reduction_hint": "Show charges in portal."
    }
    """
    result = parse_transcript_reason_analysis(content)
    assert result.tertiary_reason is None


def test_parse_transcript_reason_analysis_missing_field() -> None:
    try:
        parse_transcript_reason_analysis('{"primary_reason": "Test"}')
    except TranscriptClassificationError:
        return
    raise AssertionError("expected TranscriptClassificationError")
