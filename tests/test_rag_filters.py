from datetime import datetime, timezone

from orchestration.analysis.reason_taxonomy import parse_reason_taxonomy
from orchestration.rag.filters import (
    RetrievalFilters,
    detect_canonical_reason,
    detect_date_window,
    detect_skill,
    extract_retrieval_filters,
)
from orchestration.rag.retrieve import _build_filter_clause

# A fixed reference: Wednesday 2026-06-17 12:00 UTC.
_NOW = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


def _taxonomy():
    return parse_reason_taxonomy(
        {
            "categories": [
                {"canonical": "Remake / replacement", "aliases": ["remake", "replacement"]},
                {"canonical": "Order status", "aliases": ["order status", "where is my order"]},
            ]
        }
    )


def test_detect_yesterday() -> None:
    window = detect_date_window("calls from yesterday", now=_NOW)
    assert window is not None
    start, end = window
    assert start.date().isoformat() == "2026-06-16"
    assert end.date().isoformat() == "2026-06-16"


def test_detect_last_week_is_previous_calendar_week() -> None:
    window = detect_date_window("remake complaints last week", now=_NOW)
    assert window is not None
    start, end = window
    # Previous Mon-Sun before the week containing 2026-06-17 (Mon 2026-06-08 .. Sun 2026-06-14).
    assert start.date().isoformat() == "2026-06-08"
    assert end.date().isoformat() == "2026-06-14"


def test_detect_last_n_days() -> None:
    window = detect_date_window("what happened in the last 30 days", now=_NOW)
    assert window is not None
    start, end = window
    assert (end - start).days == 30


def test_detect_no_date_returns_none() -> None:
    assert detect_date_window("why do customers call about remakes", now=_NOW) is None


def test_detect_skill_matches_longest() -> None:
    skills = ["HD", "HD Warranty Support", "LEV Consumer"]
    assert detect_skill("show me HD Warranty Support calls", skills) == "HD Warranty Support"


def test_detect_skill_none_when_absent() -> None:
    assert detect_skill("show me calls", ["HD Warranty Support"]) is None


def test_detect_canonical_reason() -> None:
    tax = _taxonomy()
    assert detect_canonical_reason("remake complaints last week", tax) == "Remake / replacement"
    assert detect_canonical_reason("where is my order", tax) == "Order status"
    assert detect_canonical_reason("general chit chat", tax) is None


def test_extract_retrieval_filters_combines_signals() -> None:
    filters = extract_retrieval_filters(
        "remake calls for HD Warranty Support last week",
        known_skills=["HD Warranty Support"],
        taxonomy=_taxonomy(),
        now=_NOW,
    )
    assert filters.has_any
    assert filters.skill_name == "HD Warranty Support"
    assert filters.canonical_reason == "Remake / replacement"
    assert filters.start is not None and filters.end is not None


def test_extract_retrieval_filters_empty_when_nothing_detected() -> None:
    filters = extract_retrieval_filters(
        "tell me something interesting", known_skills=[], taxonomy=_taxonomy(), now=_NOW
    )
    assert not filters.has_any


def test_build_filter_clause_empty() -> None:
    joins, where, params = _build_filter_clause(None)
    assert joins == "" and where == "" and params == {}
    joins, where, params = _build_filter_clause(RetrievalFilters())
    assert joins == "" and where == "" and params == {}


def test_build_filter_clause_skill_and_date() -> None:
    filters = RetrievalFilters(skill_name="HD Warranty", start=_NOW, end=_NOW)
    joins, where, params = _build_filter_clause(filters)
    assert "k.skill_name ILIKE :flt_skill" in where
    assert "k.interaction_start >= :flt_start" in where
    assert "k.interaction_start <= :flt_end" in where
    assert params["flt_skill"] == "%HD Warranty%"
    assert "analytics_reason_taxonomy" not in joins


def test_build_filter_clause_canonical_reason_joins_taxonomy() -> None:
    filters = RetrievalFilters(canonical_reason="Remake / replacement")
    joins, where, params = _build_filter_clause(filters)
    assert "analytics_reason_taxonomy" in joins
    assert "rt.canonical_reason = :flt_reason" in where
    assert params["flt_reason"] == "Remake / replacement"
