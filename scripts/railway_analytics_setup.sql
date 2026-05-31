-- Run on Railway Postgres after combined_interactions is populated.
-- Railway dashboard -> Postgres -> Query, or: psql "$DATABASE_URL" -f scripts/railway_analytics_setup.sql

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

-- Optional: dedicated read-only DB user for the chatbot web service.
-- Replace the password before running.
--
-- CREATE USER chatbot_reader WITH PASSWORD 'your-strong-password';
-- GRANT CONNECT ON DATABASE railway TO chatbot_reader;
-- GRANT USAGE ON SCHEMA public TO chatbot_reader;
-- GRANT SELECT ON analytics_interactions, combined_interactions TO chatbot_reader;
-- GRANT SELECT ON cxone_transcripts, zendesk_tickets TO chatbot_reader;
--
-- Chatbot service DATABASE_URL:
-- postgresql+psycopg://chatbot_reader:your-strong-password@HOST:PORT/railway
