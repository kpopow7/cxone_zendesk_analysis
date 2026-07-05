from __future__ import annotations

from orchestration.analysis.reason_taxonomy import parse_reason_taxonomy
from orchestration.chatbot.tools.analytics import build_sql_from_intent
from orchestration.chatbot.tools.entities import resolve_entities


def test_resolve_entities_matches_assist_case_insensitive() -> None:
    forms = ["Assist (internal)", "Consumer Support", "Brite Support"]
    result = resolve_entities(
        engine=None,  # type: ignore[arg-type]
        form_hints=["assist"],
        known_form_names=forms,
        known_skills=[],
    )
    assert result["form_names"] == ["Assist (internal)"]


def test_resolve_entities_reason_uses_taxonomy() -> None:
    tax = parse_reason_taxonomy(
        {
            "categories": [
                {"canonical": "Remake / replacement", "aliases": ["remake", "replacement"]},
            ]
        }
    )
    result = resolve_entities(
        engine=None,  # type: ignore[arg-type]
        reason_hints=["remake order"],
        known_form_names=[],
        known_skills=[],
        taxonomy=tax,
    )
    assert result["canonical_reasons"] == ["Remake / replacement"]


def test_build_sql_top_reasons_includes_form_filter() -> None:
    sql = build_sql_from_intent(
        intent="top_reasons",
        form_names=["Assist (internal)"],
        days=7,
        limit=10,
    )
    assert sql is not None
    assert "Assist (internal)" in sql
    assert "call_reason_canonical" in sql
    assert "LIMIT 10" in sql


def test_build_sql_trend_compare_has_two_windows() -> None:
    sql = build_sql_from_intent(intent="trend_compare", days=7)
    assert sql is not None
    assert "current_count" in sql
    assert "prior_count" in sql


def test_build_sql_ticket_channels_uses_deduped_view() -> None:
    sql = build_sql_from_intent(intent="ticket_channels", days=7, limit=10)
    assert sql is not None
    assert "analytics_zendesk_ticket_channels" in sql
    assert "analytics_zendesk_tickets" not in sql
    assert "via_channel" in sql
