from __future__ import annotations

from typing import Any, Callable

from orchestration.chatbot.sql_executor import QueryResult, execute_readonly_query, format_results_for_llm
from orchestration.chatbot.sql_guard import validate_sql
from orchestration.chatbot.settings import ChatbotSettings
from sqlalchemy.engine import Engine


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _in_list_sql(column: str, values: list[str]) -> str:
    quoted = ", ".join(_sql_quote(v) for v in values)
    return f"{column} IN ({quoted})"


def _optional_created_at_filter(days: int | None) -> str:
    if days is None or days <= 0:
        return ""
    return f" AND created_at >= NOW() - INTERVAL '{int(days)} days'"


def _optional_inbound_filter(inbound_only: bool) -> str:
    if not inbound_only:
        return ""
    return (
        " AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'"
    )


def _optional_date_filter(days: int | None) -> str:
    if days is None or days <= 0:
        return ""
    return f" AND interaction_start >= NOW() - INTERVAL '{int(days)} days'"


def _optional_ticket_form_filter(form_names: list[str]) -> str:
    if not form_names:
        return ""
    return f" AND {_in_list_sql('ticket_form_name', form_names)}"


def build_sql_from_intent(
    *,
    intent: str,
    form_names: list[str] | None = None,
    skill_names: list[str] | None = None,
    canonical_reasons: list[str] | None = None,
    media_types: list[str] | None = None,
    days: int | None = None,
    limit: int = 20,
    inbound_only: bool = True,
    dimension: str | None = None,
    reason_filter: str | None = None,
) -> str | None:
    """Build vetted SQL for common analytics intents. Returns None if intent is unknown."""
    intent_key = intent.strip().lower().replace("-", "_")
    limit = max(1, min(limit, 200))
    form_names = [n for n in (form_names or []) if n]
    skill_names = [n for n in (skill_names or []) if n]
    canonical_reasons = [n for n in (canonical_reasons or []) if n]
    media_types = [n for n in (media_types or []) if n]

    filters = ""
    if form_names:
        filters += f" AND {_in_list_sql('ticket_form_name', form_names)}"
    if skill_names:
        filters += f" AND {_in_list_sql('skill_name', skill_names)}"
    if canonical_reasons:
        filters += f" AND {_in_list_sql('call_reason_canonical', canonical_reasons)}"
    if media_types:
        filters += f" AND {_in_list_sql('media_type', media_types)}"
    filters += _optional_date_filter(days)
    filters += _optional_inbound_filter(inbound_only)

    if intent_key in ("top_reasons", "top_call_reasons", "top_reasons_by_form"):
        return (
            "SELECT call_reason_canonical AS reason, COUNT(*) AS call_count\n"
            "FROM analytics_interactions\n"
            "WHERE call_reason_canonical IS NOT NULL"
            f"{filters}\n"
            "GROUP BY call_reason_canonical\n"
            "ORDER BY call_count DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("top_transcript_reasons", "top_primary_reasons"):
        skill_clause = ""
        if skill_names:
            skill_clause = f" AND {_in_list_sql('skill_name', skill_names)}"
        return (
            "SELECT primary_reason_canonical AS reason, COUNT(*) AS call_count\n"
            "FROM analytics_transcript_summaries\n"
            "WHERE primary_reason_canonical IS NOT NULL"
            f"{skill_clause}{_optional_date_filter(days)}{_optional_inbound_filter(inbound_only)}\n"
            "GROUP BY primary_reason_canonical\n"
            "ORDER BY call_count DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("count_by_dimension", "volume_by_dimension"):
        dim = (dimension or "skill_name").strip()
        allowed = {
            "skill_name",
            "ticket_form_name",
            "media_type",
            "call_reason_canonical",
            "disposition_label",
        }
        if dim not in allowed:
            dim = "skill_name"
        return (
            f"SELECT {dim} AS dimension_value, COUNT(*) AS call_count\n"
            "FROM analytics_interactions\n"
            f"WHERE {dim} IS NOT NULL"
            f"{filters}\n"
            f"GROUP BY {dim}\n"
            "ORDER BY call_count DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in (
        "ticket_channels",
        "zendesk_channel_volume",
        "ticket_volume_by_channel",
    ):
        return (
            "SELECT via_channel, COUNT(*) AS ticket_count\n"
            "FROM analytics_zendesk_ticket_channels\n"
            "WHERE via_channel IS NOT NULL"
            f"{_optional_created_at_filter(days)}"
            f"{_optional_ticket_form_filter(form_names)}\n"
            "GROUP BY via_channel\n"
            "ORDER BY ticket_count DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("top_ticket_volume_by_form", "ticket_volume_by_form"):
        return (
            "SELECT ticket_form_name, COUNT(*) AS ticket_count\n"
            "FROM analytics_zendesk_ticket_channels\n"
            "WHERE ticket_form_name IS NOT NULL"
            f"{_optional_created_at_filter(days)}"
            f"{_optional_ticket_form_filter(form_names)}\n"
            "GROUP BY ticket_form_name\n"
            "ORDER BY ticket_count DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("ticket_count_by_dimension", "ticket_volume_by_dimension"):
        dim = (dimension or "ticket_form_name").strip()
        allowed = {"ticket_form_name", "via_channel", "status", "priority"}
        if dim not in allowed:
            dim = "ticket_form_name"
        return (
            f"SELECT {dim} AS dimension_value, COUNT(*) AS ticket_count\n"
            "FROM analytics_zendesk_ticket_channels\n"
            f"WHERE {dim} IS NOT NULL"
            f"{_optional_created_at_filter(days)}"
            f"{_optional_ticket_form_filter(form_names)}\n"
            f"GROUP BY {dim}\n"
            "ORDER BY ticket_count DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("ticket_drilldown", "sample_tickets", "ticket_search"):
        channel_clause = ""
        if media_types:
            channel_clause = f" AND {_in_list_sql('via_channel', media_types)}"
        return (
            "SELECT ticket_id, created_at, via_channel, ticket_form_name, status, priority,\n"
            "       subject, description_preview, tags\n"
            "FROM analytics_zendesk_ticket_channels\n"
            "WHERE 1=1"
            f"{_optional_created_at_filter(days)}"
            f"{_optional_ticket_form_filter(form_names)}"
            f"{channel_clause}\n"
            "ORDER BY created_at DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("drilldown", "drill_down", "sample_calls"):
        reason_clause = ""
        if canonical_reasons:
            reason_clause = f" AND {_in_list_sql('primary_reason_canonical', canonical_reasons)}"
        elif reason_filter:
            safe = reason_filter.replace("'", "''")
            reason_clause = f" AND primary_reason_canonical ILIKE '%{safe}%'"
        skill_clause = ""
        if skill_names:
            skill_clause = f" AND {_in_list_sql('skill_name', skill_names)}"
        return (
            "SELECT segment_id, interaction_start, skill_name, primary_reason_canonical,\n"
            "       transcript_summary\n"
            "FROM analytics_transcript_summaries\n"
            "WHERE 1=1"
            f"{reason_clause}{skill_clause}{_optional_date_filter(days)}{_optional_inbound_filter(inbound_only)}\n"
            "ORDER BY interaction_start DESC\n"
            f"LIMIT {limit}"
        )

    if intent_key in ("trend_compare", "period_compare", "compare_periods"):
        days_window = days if days and days > 0 else 7
        return (
            "WITH compare AS (\n"
            "  SELECT\n"
            f"    COUNT(*) FILTER (WHERE interaction_start >= NOW() - INTERVAL '{days_window} days') "
            "AS current_count,\n"
            f"    COUNT(*) FILTER (WHERE interaction_start >= NOW() - INTERVAL '{days_window * 2} days'\n"
            f"                       AND interaction_start < NOW() - INTERVAL '{days_window} days') "
            "AS prior_count\n"
            "  FROM analytics_interactions\n"
            "  WHERE 1=1"
            f"{filters}\n"
            ")\n"
            "SELECT current_count, prior_count, current_count - prior_count AS change,\n"
            "       ROUND(100.0 * (current_count - prior_count) / NULLIF(prior_count, 0), 1) AS pct_change\n"
            "FROM compare"
        )

    return None


def run_analytics_sql(
    *,
    engine: Engine,
    settings: ChatbotSettings,
    intent: str | None = None,
    sql: str | None = None,
    form_names: list[str] | None = None,
    skill_names: list[str] | None = None,
    canonical_reasons: list[str] | None = None,
    media_types: list[str] | None = None,
    days: int | None = None,
    limit: int = 20,
    inbound_only: bool = True,
    dimension: str | None = None,
    reason_filter: str | None = None,
    llm_sql_generator: Callable[[str], str | None] | None = None,
    generation_prompt: str | None = None,
) -> dict[str, Any]:
    """Execute a vetted-intent query or validated custom SQL."""
    query = (sql or "").strip()
    source = "custom_sql"

    if not query and intent:
        query = build_sql_from_intent(
            intent=intent,
            form_names=form_names,
            skill_names=skill_names,
            canonical_reasons=canonical_reasons,
            media_types=media_types,
            days=days,
            limit=limit,
            inbound_only=inbound_only,
            dimension=dimension,
            reason_filter=reason_filter,
        ) or ""
        source = f"intent:{intent}"

    if not query and llm_sql_generator and generation_prompt:
        query = llm_sql_generator(generation_prompt) or ""
        source = "llm_generated"

    if not query:
        return {
            "error": "No SQL to run. Provide intent (e.g. top_reasons) or sql.",
            "row_count": 0,
            "rows": [],
        }

    validation = validate_sql(query, max_limit=settings.chatbot_max_rows)
    if not validation.ok:
        return {
            "error": validation.error,
            "sql": query,
            "row_count": 0,
            "rows": [],
            "source": source,
        }

    try:
        result = execute_readonly_query(
            engine,
            validation.sql,
            max_rows=settings.chatbot_max_rows,
            timeout_seconds=settings.chatbot_query_timeout_seconds,
        )
    except Exception as exc:
        return {
            "error": str(exc),
            "sql": validation.sql,
            "row_count": 0,
            "rows": [],
            "source": source,
        }

    return _query_result_payload(result, sql=validation.sql, source=source)


def _query_result_payload(result: QueryResult, *, sql: str, source: str) -> dict[str, Any]:
    return {
        "sql": sql,
        "source": source,
        "columns": result.columns,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "rows": result.rows,
        "preview": format_results_for_llm(result, max_chars=8000),
    }
