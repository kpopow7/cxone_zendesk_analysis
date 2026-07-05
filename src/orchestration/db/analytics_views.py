from __future__ import annotations

from sqlalchemy.engine import Engine
from sqlalchemy.sql import text

from orchestration.analysis.reason_taxonomy import reason_key_sql
from orchestration.db.schema import (
    ensure_reason_taxonomy_table,
    ensure_reduction_report_tables,
)
from orchestration.zendesk.channels import IS_PHONE_BRIDGE_TICKET_SQL

ANALYTICS_INTERACTIONS_VIEW = f"""
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
    ci.ticket_via_channel,
    ci.ticket_form_id,
    f.name AS ticket_form_name,
    ci.zendesk_promoted_fields,
    ci.call_reason,
    rt.canonical_reason AS call_reason_canonical,
    ci.call_reason_code,
    ci.call_reason_source,
    ci.disposition_code,
    ci.disposition_label,
    ci.disposition_source,
    ci.built_at
FROM combined_interactions AS ci
LEFT JOIN zendesk_ticket_forms AS f ON f.form_id = ci.ticket_form_id
LEFT JOIN analytics_reason_taxonomy AS rt
    ON rt.reason_key = {reason_key_sql('ci.call_reason')}
"""

# All Zendesk tickets (email, chat, web, phone) — not limited to CXone-linked rows.
ANALYTICS_ZENDESK_TICKETS_VIEW = f"""
CREATE OR REPLACE VIEW analytics_zendesk_tickets AS
SELECT
    z.ticket_id,
    z.created_at,
    z.updated_at,
    z.status,
    z.priority,
    z.subject,
    left(z.description, 800) AS description_preview,
    z.tags,
    z.via_channel,
    z.ticket_form_id,
    f.name AS ticket_form_name,
    {IS_PHONE_BRIDGE_TICKET_SQL} AS is_phone_bridge_ticket,
    z.promoted_fields,
    z.extracted_at
FROM zendesk_tickets AS z
LEFT JOIN zendesk_ticket_forms AS f ON f.form_id = z.ticket_form_id
"""

# Parent/detail tickets only — excludes phone-call bridge rows so channel counts do not
# double-count voice alongside the linked parent ticket.
ANALYTICS_ZENDESK_TICKET_CHANNELS_VIEW = """
CREATE OR REPLACE VIEW analytics_zendesk_ticket_channels AS
SELECT
    ticket_id,
    created_at,
    updated_at,
    status,
    priority,
    subject,
    description_preview,
    tags,
    via_channel,
    ticket_form_id,
    ticket_form_name,
    promoted_fields,
    extracted_at
FROM analytics_zendesk_tickets
WHERE is_phone_bridge_ticket = false
"""

ANALYTICS_TRANSCRIPT_SUMMARIES_VIEW = f"""
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
    rt.canonical_reason AS primary_reason_canonical,
    a.secondary_reason,
    a.tertiary_reason,
    a.reduction_hint,
    a.model AS analysis_model,
    a.analyzed_at,
    left(t.transcript_text, 2000) AS transcript_preview
FROM cxone_transcript_analysis AS a
JOIN cxone_transcripts AS t ON t.segment_id = a.segment_id
LEFT JOIN analytics_reason_taxonomy AS rt
    ON rt.reason_key = {reason_key_sql('a.primary_reason')}
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

# Reason -> outcome linkage (P0). One row per call segment with the reason it was about
# (Zendesk call_reason + transcript primary/secondary reasons) joined to its outcome:
# resolution (ticket_status), escalation (priority/tags), and repeat-contact signals
# (same caller phone, contact_no, seen more than once). Lets the chatbot turn "high volume"
# into "high cost / fixable" by grouping outcomes against any reason or slice.
#
# Notes:
# - Repeat contacts are keyed on contact_no (the caller's phone/ANI), NOT contact_id:
#   a CXone contact_id is unique per call (it groups the segments of one interaction),
#   so it never identifies the same customer calling again.
# - is_escalated is a heuristic: high/urgent ticket priority OR a ticket tag containing
#   "escalat". Tune the tag/priority rules here if the Zendesk workflow differs.
ANALYTICS_INTERACTION_OUTCOMES_VIEW = f"""
CREATE VIEW analytics_interaction_outcomes AS
WITH base AS (
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
        ci.contact_no,
        ci.call_reason,
        ci.call_reason_code,
        ci.disposition_label,
        ci.ticket_status,
        ci.ticket_priority,
        ci.ticket_tags,
        ci.ticket_form_id,
        a.primary_reason,
        a.secondary_reason,
        a.tertiary_reason,
        count(*) OVER (PARTITION BY ci.contact_no) AS contact_no_count,
        count(*) OVER (
            PARTITION BY ci.contact_no
            ORDER BY ci.interaction_start
            RANGE BETWEEN INTERVAL '30 days' PRECEDING AND CURRENT ROW
        ) - 1 AS prior_contacts_30d_raw
    FROM combined_interactions AS ci
    LEFT JOIN cxone_transcript_analysis AS a ON a.segment_id = ci.segment_id
)
SELECT
    base.segment_id,
    base.ticket_id,
    base.phone_call_ticket_id,
    base.link_method,
    base.interaction_start,
    base.interaction_end,
    base.call_direction,
    base.media_type,
    base.skill_name,
    base.team_name,
    base.agent_name,
    base.client_sentiment,
    base.agent_sentiment,
    base.contact_no,
    base.call_reason,
    base.call_reason_code,
    crt.canonical_reason AS call_reason_canonical,
    base.disposition_label,
    base.primary_reason,
    prt.canonical_reason AS primary_reason_canonical,
    base.secondary_reason,
    base.tertiary_reason,
    CASE
        WHEN crt.canonical_reason IS NULL OR prt.canonical_reason IS NULL THEN 'unknown'
        WHEN crt.canonical_reason = prt.canonical_reason THEN 'match'
        ELSE 'mismatch'
    END AS reason_match_status,
    base.ticket_status,
    base.ticket_priority,
    base.ticket_tags,
    base.ticket_form_id,
    f.name AS ticket_form_name,
    CASE
        WHEN lower(base.ticket_status) IN ('solved', 'closed') THEN 'resolved'
        WHEN lower(base.ticket_status) IN ('new', 'open', 'pending', 'hold') THEN 'unresolved'
        ELSE 'unknown'
    END AS resolution_status,
    coalesce(lower(base.ticket_status) IN ('solved', 'closed'), false) AS is_resolved,
    coalesce(lower(base.ticket_status) IN ('new', 'open', 'pending', 'hold'), false) AS is_open,
    (
        lower(coalesce(base.ticket_priority, '')) IN ('high', 'urgent')
        OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(base.ticket_tags) = 'array'
                     THEN base.ticket_tags ELSE '[]'::jsonb END
            ) AS tag
            WHERE tag ILIKE '%escalat%'
        )
    ) AS is_escalated,
    CASE
        WHEN base.contact_no IS NULL OR base.contact_no = '' THEN NULL
        ELSE base.contact_no_count
    END AS contact_interaction_count,
    (
        base.contact_no IS NOT NULL
        AND base.contact_no <> ''
        AND base.contact_no_count > 1
    ) AS is_repeat_contact,
    CASE
        WHEN base.contact_no IS NULL OR base.contact_no = '' THEN NULL
        ELSE greatest(base.prior_contacts_30d_raw, 0)
    END AS prior_contacts_30d
FROM base
LEFT JOIN zendesk_ticket_forms AS f ON f.form_id = base.ticket_form_id
LEFT JOIN analytics_reason_taxonomy AS crt
    ON crt.reason_key = {reason_key_sql('base.call_reason')}
LEFT JOIN analytics_reason_taxonomy AS prt
    ON prt.reason_key = {reason_key_sql('base.primary_reason')}
"""

# Pre-aggregated reason -> outcome rates so "which reasons are high cost / fixable?" is a
# single query. Grouped by the unified Zendesk call_reason; for transcript-derived reasons
# group analytics_interaction_outcomes by primary_reason instead.
ANALYTICS_REASON_OUTCOMES_VIEW = """
CREATE VIEW analytics_reason_outcomes AS
SELECT
    call_reason,
    count(*) AS call_count,
    count(DISTINCT contact_no) FILTER (
        WHERE contact_no IS NOT NULL AND contact_no <> ''
    ) AS distinct_callers,
    count(*) FILTER (WHERE is_resolved) AS resolved_count,
    round(100.0 * count(*) FILTER (WHERE is_resolved) / nullif(count(*), 0), 1) AS resolved_pct,
    count(*) FILTER (WHERE is_open) AS unresolved_count,
    round(100.0 * count(*) FILTER (WHERE is_open) / nullif(count(*), 0), 1) AS unresolved_pct,
    count(*) FILTER (WHERE is_escalated) AS escalated_count,
    round(100.0 * count(*) FILTER (WHERE is_escalated) / nullif(count(*), 0), 1) AS escalated_pct,
    count(*) FILTER (WHERE is_repeat_contact) AS repeat_contact_count,
    round(
        100.0 * count(*) FILTER (WHERE is_repeat_contact) / nullif(count(*), 0), 1
    ) AS repeat_contact_pct
FROM analytics_interaction_outcomes
WHERE call_reason IS NOT NULL AND call_reason <> ''
GROUP BY call_reason
"""

# Canonical reason -> outcome rates (P1 taxonomy). Same shape as analytics_reason_outcomes but
# grouped on the controlled canonical_reason (transcript primary preferred, Zendesk call_reason
# as fallback) so rankings are not fragmented by free-text phrasing. Prefer this view for "top
# reasons" / "worst reasons" questions; it is the trustworthy ranking.
ANALYTICS_CANONICAL_REASON_OUTCOMES_VIEW = """
CREATE VIEW analytics_canonical_reason_outcomes AS
WITH labeled AS (
    SELECT
        coalesce(primary_reason_canonical, call_reason_canonical) AS canonical_reason,
        contact_no,
        is_resolved,
        is_open,
        is_escalated,
        is_repeat_contact
    FROM analytics_interaction_outcomes
)
SELECT
    canonical_reason,
    count(*) AS call_count,
    count(DISTINCT contact_no) FILTER (
        WHERE contact_no IS NOT NULL AND contact_no <> ''
    ) AS distinct_callers,
    count(*) FILTER (WHERE is_resolved) AS resolved_count,
    round(100.0 * count(*) FILTER (WHERE is_resolved) / nullif(count(*), 0), 1) AS resolved_pct,
    count(*) FILTER (WHERE is_open) AS unresolved_count,
    round(100.0 * count(*) FILTER (WHERE is_open) / nullif(count(*), 0), 1) AS unresolved_pct,
    count(*) FILTER (WHERE is_escalated) AS escalated_count,
    round(100.0 * count(*) FILTER (WHERE is_escalated) / nullif(count(*), 0), 1) AS escalated_pct,
    count(*) FILTER (WHERE is_repeat_contact) AS repeat_contact_count,
    round(
        100.0 * count(*) FILTER (WHERE is_repeat_contact) / nullif(count(*), 0), 1
    ) AS repeat_contact_pct
FROM labeled
WHERE canonical_reason IS NOT NULL
GROUP BY canonical_reason
"""

# Reconciliation (P1): how often the Zendesk form reason and the transcript reason agree once
# both are mapped to the canonical vocabulary. Only rows where BOTH reasons are present and
# mappable are comparable; a low agree_pct flags miscategorized tickets or taxonomy gaps.
ANALYTICS_REASON_RECONCILIATION_VIEW = """
CREATE VIEW analytics_reason_reconciliation AS
SELECT
    call_reason_canonical,
    count(*) AS comparable_calls,
    count(*) FILTER (WHERE reason_match_status = 'match') AS agree_count,
    round(
        100.0 * count(*) FILTER (WHERE reason_match_status = 'match') / nullif(count(*), 0), 1
    ) AS agree_pct,
    count(*) FILTER (WHERE reason_match_status = 'mismatch') AS disagree_count,
    round(
        100.0 * count(*) FILTER (WHERE reason_match_status = 'mismatch') / nullif(count(*), 0), 1
    ) AS disagree_pct
FROM analytics_interaction_outcomes
WHERE call_reason_canonical IS NOT NULL
  AND primary_reason_canonical IS NOT NULL
GROUP BY call_reason_canonical
"""

# Tagging-accuracy QA drill-down (P2): the specific interactions where the Zendesk form reason
# (what the agent tagged) and the transcript-derived reason (what the call was actually about)
# map to DIFFERENT canonical categories. Where analytics_reason_reconciliation gives the rate,
# this lists the individual tickets to review, so a QA analyst can validate / re-tag them.
ANALYTICS_REASON_MISMATCHES_VIEW = """
CREATE VIEW analytics_reason_mismatches AS
SELECT
    segment_id,
    ticket_id,
    interaction_start,
    media_type,
    skill_name,
    team_name,
    agent_name,
    ticket_form_name,
    call_reason,
    call_reason_canonical AS tagged_reason_canonical,
    primary_reason,
    primary_reason_canonical AS transcript_reason_canonical,
    ticket_status,
    resolution_status,
    is_escalated,
    is_repeat_contact
FROM analytics_interaction_outcomes
WHERE reason_match_status = 'mismatch'
"""


def ensure_analytics_views(engine: Engine) -> None:
    """Create or refresh analytics views used by the chatbot and reporting."""
    # The reduction recommendations view reads these tables; make sure they exist first.
    ensure_reduction_report_tables(engine)
    # Views LEFT JOIN the reason taxonomy map; it must exist (even if empty) before they build.
    ensure_reason_taxonomy_table(engine)
    with engine.begin() as connection:
        # Postgres CREATE OR REPLACE cannot insert columns mid-view; drop first.
        connection.execute(text("DROP VIEW IF EXISTS analytics_interactions CASCADE"))
        connection.execute(text(ANALYTICS_INTERACTIONS_VIEW))
        connection.execute(text("DROP VIEW IF EXISTS analytics_zendesk_ticket_channels CASCADE"))
        connection.execute(text("DROP VIEW IF EXISTS analytics_zendesk_tickets CASCADE"))
        connection.execute(text(ANALYTICS_ZENDESK_TICKETS_VIEW))
        connection.execute(text(ANALYTICS_ZENDESK_TICKET_CHANNELS_VIEW))
        connection.execute(text("DROP VIEW IF EXISTS analytics_transcript_summaries CASCADE"))
        connection.execute(text(ANALYTICS_TRANSCRIPT_SUMMARIES_VIEW))
        connection.execute(text("DROP VIEW IF EXISTS analytics_reduction_recommendations CASCADE"))
        connection.execute(text(ANALYTICS_REDUCTION_RECOMMENDATIONS_VIEW))
        # reason -> outcome: drop the aggregates first (they depend on the per-segment view).
        connection.execute(text("DROP VIEW IF EXISTS analytics_reason_outcomes CASCADE"))
        connection.execute(text("DROP VIEW IF EXISTS analytics_canonical_reason_outcomes CASCADE"))
        connection.execute(text("DROP VIEW IF EXISTS analytics_reason_reconciliation CASCADE"))
        connection.execute(text("DROP VIEW IF EXISTS analytics_reason_mismatches CASCADE"))
        connection.execute(text("DROP VIEW IF EXISTS analytics_interaction_outcomes CASCADE"))
        connection.execute(text(ANALYTICS_INTERACTION_OUTCOMES_VIEW))
        connection.execute(text(ANALYTICS_REASON_OUTCOMES_VIEW))
        connection.execute(text(ANALYTICS_CANONICAL_REASON_OUTCOMES_VIEW))
        connection.execute(text(ANALYTICS_REASON_RECONCILIATION_VIEW))
        connection.execute(text(ANALYTICS_REASON_MISMATCHES_VIEW))
