-- Run on Railway Postgres (or local Docker) after pipeline tables are populated.
-- Railway dashboard -> Postgres -> Query, or: psql "$DATABASE_URL" -f scripts/railway_analytics_setup.sql
-- Also applied automatically by init_db.py and sync_to_railway.py.

DROP VIEW IF EXISTS analytics_interactions CASCADE;

CREATE VIEW analytics_interactions AS
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
FROM combined_interactions;

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
JOIN cxone_transcripts AS t ON t.segment_id = a.segment_id;

-- Optional: dedicated read-only DB user for the chatbot web service.
-- Replace the password before running.
--
-- CREATE USER chatbot_reader WITH PASSWORD 'your-strong-password';
-- GRANT CONNECT ON DATABASE railway TO chatbot_reader;
-- GRANT USAGE ON SCHEMA public TO chatbot_reader;
-- GRANT SELECT ON analytics_interactions, analytics_transcript_summaries,
--   combined_interactions, cxone_transcript_analysis, cxone_transcripts, zendesk_tickets
--   TO chatbot_reader;
--
-- Chatbot service DATABASE_URL:
-- postgresql+psycopg://chatbot_reader:your-strong-password@HOST:PORT/railway
