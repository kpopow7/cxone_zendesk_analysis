from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.sql import text

from orchestration.db.schema import ensure_reduction_report_tables

ANALYTICS_INTERACTIONS_VIEW = """
CREATE OR REPLACE VIEW analytics_interactions AS
SELECT
    ci.segment_id,
    ci.ticket_id,
    ci.phone_call_ticket_id,
    ci.link_method,
    ci.interaction_start,
    ci.interaction_end,
    ci.call_direction,
    ci.media_type,
    ci.skill_name,
    ci.team_name,
    ci.agent_name,
    ci.client_sentiment,
    ci.agent_sentiment,
    ci.segment_summary,
    left(ci.transcript_text, 2000) AS transcript_preview,
    ci.ticket_subject,
    ci.ticket_description,
    ci.ticket_status,
    ci.ticket_priority,
    ci.ticket_tags,
    ci.ticket_form_id,
    f.name AS ticket_form_name,
    ci.zendesk_promoted_fields,
    ci.call_reason,
    ci.call_reason_code,
    ci.call_reason_source,
    ci.disposition_code,
    ci.disposition_label,
    ci.disposition_source,
    ci.built_at
FROM combined_interactions AS ci
LEFT JOIN zendesk_ticket_forms AS f ON f.form_id = ci.ticket_form_id
"""

ANALYTICS_TRANSCRIPT_SUMMARIES_VIEW = """
CREATE OR REPLACE VIEW analytics_transcript_summaries AS
SELECT
    a.segment_id,
    t.interaction_start,
    t.interaction_end,
    t.call_direction,
    t.media_type,
    t.skill_name,
    t.team_name,
    t.agent_name,
    t.client_sentiment,
    t.agent_sentiment,
    a.transcript_summary,
    a.primary_reason,
    a.secondary_reason,
    a.tertiary_reason,
    a.reduction_hint,
    a.model AS analysis_model,
    a.analyzed_at,
    left(t.transcript_text, 2000) AS transcript_preview
FROM cxone_transcript_analysis AS a
JOIN cxone_transcripts AS t ON t.segment_id = a.segment_id
"""

# Surfaces the most recent reduction report run as flat rows so the chatbot can answer
# "what's driving contacts and what should we do?" — ranked reasons + recommendations.
ANALYTICS_REDUCTION_RECOMMENDATIONS_VIEW = """
CREATE OR REPLACE VIEW analytics_reduction_recommendations AS
SELECT
    rep.report_id,
    rep.generated_at,
    rep.timeframe_label,
    rep.timeframe_start,
    rep.timeframe_end,
    rep.transcripts_analyzed,
    rsn.rank,
    rsn.primary_reason,
    rsn.call_count,
    rsn.share_pct,
    rsn.importance_score,
    rsn.negative_sentiment_pct,
    rsn.recommendation_source,
    rsn.recommendations_text,
    rsn.recommendations,
    rsn.reduction_hints,
    rsn.secondary,
    rsn.sample_segment_ids
FROM transcript_reduction_report_reasons AS rsn
JOIN transcript_reduction_reports AS rep ON rep.report_id = rsn.report_id
WHERE rep.report_id = (
    SELECT report_id
    FROM transcript_reduction_reports
    ORDER BY generated_at DESC, report_id DESC
    LIMIT 1
)
"""


def ensure_analytics_views(engine: Engine) -> None:
    """Create or refresh analytics views used by the chatbot and reporting."""
    # The reduction recommendations view reads these tables; make sure they exist first.
    ensure_reduction_report_tables(engine)
    with engine.begin() as connection:
        # Postgres CREATE OR REPLACE cannot insert columns mid-view; drop first.
        connection.execute(text("DROP VIEW IF EXISTS analytics_interactions CASCADE"))
        connection.execute(text(ANALYTICS_INTERACTIONS_VIEW))
        connection.execute(text(ANALYTICS_TRANSCRIPT_SUMMARIES_VIEW))
        connection.execute(text("DROP VIEW IF EXISTS analytics_reduction_recommendations CASCADE"))
        connection.execute(text(ANALYTICS_REDUCTION_RECOMMENDATIONS_VIEW))
