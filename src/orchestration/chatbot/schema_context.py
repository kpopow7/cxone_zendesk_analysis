from __future__ import annotations

SCHEMA_CONTEXT = """
You query PostgreSQL for contact-center analytics. Use ONLY the tables/views below.

## Primary table: analytics_interactions (preferred)
Denormalized CXone calls linked to Zendesk parent tickets. One row per call segment.

Columns:
- segment_id (text, PK) — unique call segment
- interaction_start, interaction_end (timestamptz) — filter dates on interaction_start
- call_direction (text) — e.g. IN_BOUND, OUT_BOUND
- media_type (text) — e.g. PhoneCall
- skill_name, team_name, agent_name (text)
- client_sentiment, agent_sentiment (text)
- segment_summary (text) — CXone auto-summary
- transcript_preview (text) — first ~2000 chars of transcript (not full transcript)
- ticket_id, phone_call_ticket_id (bigint)
- link_method (text) — call_object_to_parent = fully linked; unmatched = no Zendesk match
- ticket_subject, ticket_description, ticket_status, ticket_priority (text)
- ticket_tags (jsonb array)
- call_reason (text) — unified reason across all Zendesk forms (human-readable)
- call_reason_code (text) — raw Zendesk reason value
- call_reason_source (text) — source field, e.g. cf_reason_for_contact_consumer
- disposition_label (text) — unified disposition label across all forms
- disposition_code (text) — raw Zendesk disposition code
- disposition_source (text) — source field, e.g. cf_disposition_dealer
- zendesk_promoted_fields (jsonb) — raw Zendesk custom fields (use only for drill-down)

## Transcript-only LLM summaries: analytics_transcript_summaries (preferred for transcript-derived reasons)
One row per classified call segment from run_transcript_summary.py (cxone_transcript_analysis joined to cxone_transcripts).
Use when the user asks about transcript-based reasons, sub-reasons, or per-call LLM summaries — not Zendesk ticket fields.

Columns:
- segment_id (text, PK)
- interaction_start, interaction_end (timestamptz)
- call_direction, media_type, skill_name, team_name, agent_name (text)
- client_sentiment, agent_sentiment (text)
- transcript_summary (text) — LLM summary of the call
- primary_reason, secondary_reason, tertiary_reason (text) — hierarchical call reasons from transcript
- reduction_hint (text) — one-line suggestion to reduce similar contacts
- analysis_model (text), analyzed_at (timestamptz)
- transcript_preview (text) — first ~2000 chars of transcript

## Fallback: combined_interactions
Same as analytics_interactions but includes full transcript_text (large). Prefer analytics_interactions.

## Business rules
- For call reasons: use call_reason (not individual cf_reason_* JSON keys)
- For dispositions: use disposition_label (not individual cf_disposition_* JSON keys)
- Inbound calls: upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
- Default to inbound PhoneCall when user asks about "calls" without specifying direction
- Prefer link_method = 'call_object_to_parent' for ticket-enriched analysis unless user wants all segments
- For transcript-only LLM reasons (primary/secondary/tertiary), use analytics_transcript_summaries — not call_reason from Zendesk
- Use date ranges on interaction_start (timestamptz). "Last week" = previous Mon-Sun UTC; "yesterday" = prior calendar day UTC
- Aggregate (COUNT, GROUP BY) for volume questions; LIMIT row samples for examples

## SQL rules (mandatory)
- SELECT or WITH ... SELECT only
- Always include LIMIT (max 200 rows) unless pure aggregation returning few groups
- No INSERT, UPDATE, DELETE, DROP, or DDL
- No semicolons (single statement only)
- Prefer analytics_interactions over combined_interactions

## Example queries

Top call reasons last 7 days (inbound):
SELECT call_reason, COUNT(*) AS call_count
FROM analytics_interactions
WHERE interaction_start >= NOW() - INTERVAL '7 days'
  AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
  AND call_reason IS NOT NULL
GROUP BY call_reason
ORDER BY call_count DESC
LIMIT 20;

Top dispositions:
SELECT disposition_label, COUNT(*) AS n
FROM analytics_interactions
WHERE link_method = 'call_object_to_parent'
  AND disposition_label IS NOT NULL
GROUP BY disposition_label
ORDER BY n DESC
LIMIT 15;

Top skills last 7 days (inbound):
SELECT skill_name, COUNT(*) AS call_count
FROM analytics_interactions
WHERE interaction_start >= NOW() - INTERVAL '7 days'
  AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
GROUP BY skill_name
ORDER BY call_count DESC
LIMIT 20;

Top transcript-derived primary reasons last 7 days:
SELECT primary_reason, COUNT(*) AS call_count
FROM analytics_transcript_summaries
WHERE interaction_start >= NOW() - INTERVAL '7 days'
  AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
GROUP BY primary_reason
ORDER BY call_count DESC
LIMIT 20;

Sample per-call transcript summaries for a primary reason:
SELECT segment_id, interaction_start, skill_name, primary_reason, secondary_reason,
       tertiary_reason, transcript_summary
FROM analytics_transcript_summaries
WHERE primary_reason ILIKE '%remake%'
ORDER BY interaction_start DESC
LIMIT 10;
""".strip()


def build_schema_prompt() -> str:
    return SCHEMA_CONTEXT
