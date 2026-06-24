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

-- Ranked reduction reports (reasons + recommendations). Populated by
-- scripts/run_transcript_summary.py --full-report and synced via sync_to_railway.py.
CREATE TABLE IF NOT EXISTS transcript_reduction_reports (
    report_id            BIGSERIAL PRIMARY KEY,
    generated_at         TIMESTAMPTZ NOT NULL,
    timeframe_preset     VARCHAR(64),
    timeframe_label      VARCHAR(255),
    timeframe_start      TIMESTAMPTZ,
    timeframe_end        TIMESTAMPTZ,
    transcripts_analyzed BIGINT NOT NULL DEFAULT 0,
    reason_count         BIGINT NOT NULL DEFAULT 0,
    filters              JSONB NOT NULL DEFAULT '{}'::jsonb,
    totals               JSONB NOT NULL DEFAULT '{}'::jsonb,
    classification       JSONB NOT NULL DEFAULT '{}'::jsonb,
    llm                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    insights             JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transcript_reduction_report_reasons (
    id                     BIGSERIAL PRIMARY KEY,
    report_id              BIGINT NOT NULL,
    generated_at           TIMESTAMPTZ NOT NULL,
    timeframe_label        VARCHAR(255),
    rank                   BIGINT NOT NULL DEFAULT 0,
    primary_reason         VARCHAR(512) NOT NULL DEFAULT '',
    primary_reason_key     VARCHAR(512) NOT NULL DEFAULT '',
    call_count             BIGINT NOT NULL DEFAULT 0,
    share_pct              DOUBLE PRECISION NOT NULL DEFAULT 0,
    importance_score       DOUBLE PRECISION NOT NULL DEFAULT 0,
    negative_sentiment_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    recommendation_source  VARCHAR(32) NOT NULL DEFAULT 'rules',
    recommendations        JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommendations_text   TEXT,
    reduction_hints        JSONB NOT NULL DEFAULT '[]'::jsonb,
    secondary              JSONB NOT NULL DEFAULT '[]'::jsonb,
    sample_summaries       JSONB NOT NULL DEFAULT '[]'::jsonb,
    sample_segment_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reduction_reasons_report_id
    ON transcript_reduction_report_reasons (report_id);

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
);

-- Optional: dedicated read-only DB user for the chatbot web service.
-- Replace the password before running.
--
-- CREATE USER chatbot_reader WITH PASSWORD 'your-strong-password';
-- GRANT CONNECT ON DATABASE railway TO chatbot_reader;
-- GRANT USAGE ON SCHEMA public TO chatbot_reader;
-- GRANT SELECT ON analytics_interactions, analytics_transcript_summaries,
--   analytics_reduction_recommendations, combined_interactions, cxone_transcript_analysis,
--   cxone_transcripts, transcript_reduction_reports, transcript_reduction_report_reasons,
--   zendesk_tickets, zendesk_ticket_forms
--   TO chatbot_reader;
--
-- Chatbot service DATABASE_URL:
-- postgresql+psycopg://chatbot_reader:your-strong-password@HOST:PORT/railway
