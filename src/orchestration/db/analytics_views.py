from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.sql import text

ANALYTICS_INTERACTIONS_VIEW = """
CREATE OR REPLACE VIEW analytics_interactions AS
SELECT
    segment_id,
    ticket_id,
    phone_call_ticket_id,
    link_method,
    interaction_start,
    interaction_end,
    call_direction,
    media_type,
    skill_name,
    team_name,
    agent_name,
    client_sentiment,
    agent_sentiment,
    segment_summary,
    left(transcript_text, 2000) AS transcript_preview,
    ticket_subject,
    ticket_description,
    ticket_status,
    ticket_priority,
    ticket_tags,
    zendesk_promoted_fields,
    call_reason,
    call_reason_code,
    call_reason_source,
    disposition_code,
    disposition_label,
    disposition_source,
    built_at
FROM combined_interactions
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


def ensure_analytics_views(engine: Engine) -> None:
    """Create or refresh analytics views used by the chatbot and reporting."""
    with engine.begin() as connection:
        # Postgres CREATE OR REPLACE cannot insert columns mid-view; drop first.
        connection.execute(text("DROP VIEW IF EXISTS analytics_interactions CASCADE"))
        connection.execute(text(ANALYTICS_INTERACTIONS_VIEW))
        connection.execute(text(ANALYTICS_TRANSCRIPT_SUMMARIES_VIEW))
