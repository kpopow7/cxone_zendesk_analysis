-- Run on Railway Postgres (or local Docker) after pipeline tables are populated.
-- Railway dashboard -> Postgres -> Query, or: psql "$DATABASE_URL" -f scripts/railway_analytics_setup.sql
-- Also applied automatically by init_db.py and sync_to_railway.py.

DROP VIEW IF EXISTS analytics_interactions CASCADE;

CREATE VIEW analytics_interactions AS
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
LEFT JOIN zendesk_ticket_forms AS f ON f.form_id = ci.ticket_form_id;

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
--   combined_interactions, cxone_transcript_analysis, cxone_transcripts, zendesk_tickets,
--   zendesk_ticket_forms
--   TO chatbot_reader;
--
-- Chatbot service DATABASE_URL:
-- postgresql+psycopg://chatbot_reader:your-strong-password@HOST:PORT/railway
