from __future__ import annotations

SCHEMA_CONTEXT = """
You query PostgreSQL for contact-center analytics. Use ONLY the tables/views below.

## Choosing a data source (read first)
- PHONE CALL questions (transcripts, call reasons, agent/skill on calls) → analytics_interactions
- ZENDESK TICKET questions (email/chat/web volume, ticket status, by channel, ticket form types) →
  analytics_zendesk_ticket_channels (deduped, no phone bridge) or analytics_zendesk_tickets
- OVERALL / all-channel contact volume → analytics_zendesk_ticket_channels (covers all channels),
  NOT analytics_interactions (phone calls only)
analytics_interactions is NOT the whole dataset — it only contains CXone phone calls linked to a
parent ticket. Do not use it for email/chat/web tickets or total contact volume.

## analytics_interactions (phone calls only)
Denormalized CXone calls linked to Zendesk parent tickets. One row per call segment.

Columns:
- segment_id (text, PK) — unique call segment
- interaction_start, interaction_end (timestamptz) — filter dates on interaction_start
- call_direction (text) — e.g. IN_BOUND, OUT_BOUND
- media_type (text) — channel, e.g. PhoneCall, Email, Chat (most rows are PhoneCall)
- skill_name, team_name, agent_name (text)
- client_sentiment, agent_sentiment (text)
- segment_summary (text) — CXone auto-summary
- transcript_preview (text) — first ~2000 chars of transcript (not full transcript)
- ticket_id, phone_call_ticket_id (bigint)
- link_method (text) — call_object_to_parent = fully linked; unmatched = no Zendesk match
- ticket_subject, ticket_description, ticket_status, ticket_priority (text)
- ticket_tags (jsonb array)
- ticket_via_channel (text) — Zendesk channel on the parent ticket: voice=phone, mail=email, web=web form, chat=chat. Phone-bridge tickets tagged "agent created" are stored as voice even when Zendesk reports another channel
- ticket_form_id (bigint) — numeric Zendesk ticket form id
- ticket_form_name (text) — human ticket form type, e.g. "Assist (Internal)"; use this to group/filter by form type
- call_reason (text) — unified reason across all Zendesk forms (human-readable, free text)
- call_reason_canonical (text) — controlled taxonomy label for call_reason (e.g. "Order status"). Prefer this for grouping/ranking reasons so phrasing variants are consolidated
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
- primary_reason, secondary_reason, tertiary_reason (text) — hierarchical call reasons from transcript (free text)
- primary_reason_canonical (text) — controlled taxonomy label for primary_reason. Prefer this for grouping/ranking transcript reasons
- reduction_hint (text) — one-line suggestion to reduce similar contacts
- analysis_model (text), analyzed_at (timestamptz)
- transcript_preview (text) — first ~2000 chars of transcript

## Reduction recommendations: analytics_reduction_recommendations (use for "what should we do / how to reduce")
Ranked root-cause reasons WITH recommended fixes from the latest reduction report run
(run_transcript_summary.py --full-report). Use this when the user asks what is driving contacts
AND what to do about it, how to reduce volume, top reasons with recommendations, or "fixes".
This view returns only the most recent report run (already ranked by rank ascending = highest volume first).

Columns:
- report_id (bigint), generated_at (timestamptz) — when the report was produced
- timeframe_label (text) — e.g. "last week (2026-06-09 → 2026-06-15)"; timeframe_start, timeframe_end (timestamptz)
- transcripts_analyzed (int) — calls analyzed in this report
- rank (int) — 1 = top reason by volume
- primary_reason (text) — root-cause reason
- call_count (int) — calls for this reason; share_pct (float) — % of analyzed calls
- importance_score (float, 0–100); negative_sentiment_pct (float)
- recommendation_source (text) — 'llm' or 'rules'
- recommendations_text (text) — newline-separated recommended fixes (prefer this for display)
- recommendations (jsonb array) — same fixes as an array
- reduction_hints (jsonb array) — sample per-call reduction hints
- secondary (jsonb) — secondary/tertiary breakdown for this reason
- sample_segment_ids (jsonb array) — example calls for drill-down

This view is pre-aggregated; do NOT add GROUP BY. Order by rank for top reasons. It already
contains only the latest run, so no date filter is needed unless the user asks for history
(query transcript_reduction_reports / transcript_reduction_report_reasons for older runs).

## Reason -> outcome: analytics_reason_outcomes (use for "is this reason costly / are we fixing it")
Pre-aggregated outcome rates per call reason — turns "high volume" into "high cost / fixable".
Grouped by the unified Zendesk call_reason. Use when the user asks which reasons drive repeat
contacts, escalations, or stay unresolved, or asks about resolution/escalation/callback rates.

Columns (one row per call_reason):
- call_reason (text) — the reason callers contacted about
- call_count (bigint) — calls with this reason; distinct_callers (bigint) — unique phone numbers
- resolved_count (bigint), resolved_pct (numeric) — ticket_status solved/closed
- unresolved_count (bigint), unresolved_pct (numeric) — ticket_status new/open/pending/hold
- escalated_count (bigint), escalated_pct (numeric) — high/urgent priority or an "escalat" tag
- repeat_contact_count (bigint), repeat_contact_pct (numeric) — calls from a phone number seen >1 time

This view is pre-aggregated; do NOT add GROUP BY. Order by call_count (volume), escalated_pct,
repeat_contact_pct, or unresolved_pct depending on what the user wants to prioritize.

## Per-call outcomes: analytics_interaction_outcomes (use for slicing outcomes by any dimension)
One row per call segment with its reason joined to its outcome. Use this when the user wants
outcomes grouped by something other than call_reason (e.g. by transcript primary_reason, skill,
team, form type, date) or wants the example calls behind an outcome.

Columns:
- segment_id (text), ticket_id (bigint), interaction_start (timestamptz)
- call_direction, media_type, skill_name, team_name, agent_name (text)
- client_sentiment, agent_sentiment (text)
- contact_no (text) — caller phone/ANI; the key for repeat contacts (NOT contact_id)
- call_reason (text) — Zendesk reason; primary_reason, secondary_reason, tertiary_reason (text) — transcript reasons
- call_reason_canonical (text) — taxonomy label for the Zendesk reason; primary_reason_canonical (text) — taxonomy label for the transcript reason
- reason_match_status (text) — 'match' | 'mismatch' | 'unknown' (do the Zendesk and transcript canonical reasons agree)
- ticket_status, ticket_priority (text), ticket_tags (jsonb), ticket_form_name (text)
- resolution_status (text) — 'resolved' | 'unresolved' | 'unknown'
- is_resolved (bool), is_open (bool) — booleans for FILTER/aggregation
- is_escalated (bool) — high/urgent priority OR a ticket tag containing "escalat"
- is_repeat_contact (bool) — this caller (contact_no) appears more than once in the data
- contact_interaction_count (int) — total calls by this caller; prior_contacts_30d (int) — calls by the same caller in the trailing 30 days before this one

## Canonical reason -> outcome: analytics_canonical_reason_outcomes (PREFER for "top reasons" rankings)
Same outcome rates as analytics_reason_outcomes but grouped on the CONTROLLED canonical_reason
(taxonomy label), so free-text phrasing variants are consolidated into trustworthy categories.
Use this for "what are the top reasons", "biggest reasons", "worst reasons" — it does not split
"order status" / "order status check" / "where is my order" into separate rows.

Columns (one row per canonical_reason): canonical_reason (text), call_count, distinct_callers,
resolved_count/resolved_pct, unresolved_count/unresolved_pct, escalated_count/escalated_pct,
repeat_contact_count/repeat_contact_pct. Pre-aggregated; do NOT add GROUP BY. Order by call_count
(volume) or a *_pct column to prioritize.

## Reason reconciliation: analytics_reason_reconciliation (use for "do agent tags match the calls")
How often the Zendesk form reason and the transcript-derived reason agree once both are mapped to
the canonical vocabulary. Surfaces miscategorized tickets / tagging-accuracy issues.
Columns (one row per call_reason_canonical): call_reason_canonical (text), comparable_calls,
agree_count, agree_pct, disagree_count, disagree_pct. Pre-aggregated; do NOT add GROUP BY.
Order by disagree_pct DESC (with a comparable_calls floor) to find the worst-tagged reasons.

## Tagging-accuracy drill-down: analytics_reason_mismatches (use for "which specific tickets are mis-tagged")
The individual interactions where the agent-tagged Zendesk reason and the transcript-derived reason
map to DIFFERENT canonical categories — the concrete tickets behind analytics_reason_reconciliation's
disagree counts, for a QA analyst to review/re-tag. One row per mismatched segment.
Columns: segment_id (text), ticket_id (bigint), interaction_start (timestamptz), media_type,
skill_name, team_name, agent_name, ticket_form_name (text), call_reason (text, raw tagged reason),
tagged_reason_canonical (text, what the agent tagged), primary_reason (text, raw transcript reason),
transcript_reason_canonical (text, what the call was about), ticket_status, resolution_status (text),
is_escalated (bool), is_repeat_contact (bool). Note: a row where tagged_reason_canonical or
transcript_reason_canonical is 'Other / Uncategorized' usually reflects a taxonomy gap rather than a
true agent mis-tag; exclude those (both sides <> 'Other / Uncategorized') for confident mis-tags.

## Reason taxonomy: analytics_reason_taxonomy (lookup)
The controlled vocabulary map. Columns: reason_key (text, normalized free text), reason_display
(text), canonical_reason (text), sources (text), call_count (bigint). Query directly only to list
canonical categories or inspect how a raw reason maps; otherwise use the *_canonical columns above.

## All Zendesk tickets: analytics_zendesk_tickets
One row per Zendesk ticket across ALL channels — not limited to CXone-linked phone calls.
Includes phone-call bridge tickets (Phone Call form / "agent created" tag) used to link
CXone calls to parent tickets. Do NOT use this view for channel volume counts.

Columns:
- ticket_id (bigint, PK), created_at, updated_at (timestamptz)
- status, priority, subject (text), description_preview (text)
- tags (jsonb array)
- via_channel (text) — voice=phone, mail=email, web=web form, chat=chat
- ticket_form_id (bigint), ticket_form_name (text)
- is_phone_bridge_ticket (bool) — true for Phone Call form / "agent created" bridge rows
- promoted_fields (jsonb) — promoted Zendesk custom fields

## Zendesk channel counts: analytics_zendesk_ticket_channels (PREFER for ticket volume by channel)
Parent/detail tickets only — excludes phone-call bridge rows so voice is not double-counted
with the linked parent ticket. Use for email/chat/web/phone ticket volume by via_channel.

Columns: same as analytics_zendesk_tickets except is_phone_bridge_ticket is omitted
(all rows are non-bridge parent/detail tickets).

## Fallback: combined_interactions
Same as analytics_interactions but includes full transcript_text (large). Prefer analytics_interactions.

## Ticket form types: zendesk_ticket_forms (lookup)
Maps Zendesk ticket forms to readable names. Columns: form_id (bigint, PK), name (text), active (bool).
analytics_interactions.ticket_form_name already resolves this, so prefer filtering/grouping on
analytics_interactions.ticket_form_name. Query zendesk_ticket_forms directly only to list available form types.

## Business rules
- To group or filter tickets by "form type" (e.g. "Assist (Internal)"), use ticket_form_name on analytics_interactions
- For call reasons: use call_reason (not individual cf_reason_* JSON keys)
- For ranking/"top reasons" questions, PREFER the canonical columns (call_reason_canonical / primary_reason_canonical) or analytics_canonical_reason_outcomes so phrasing variants are consolidated; use raw call_reason / primary_reason only when the user wants the exact free-text wording
- For "do agent tags match the call" or tagging-accuracy questions, use analytics_reason_reconciliation for the rates; use analytics_reason_mismatches to list the specific mis-tagged tickets (exclude 'Other / Uncategorized' on both sides for confident mis-tags)
- For dispositions: use disposition_label (not individual cf_disposition_* JSON keys)
- Inbound calls: upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
- media_type identifies the CXone channel (e.g. PhoneCall, Email, Chat). Most combined rows are PhoneCall; filter media_type = 'PhoneCall' only when the user specifically asks about phone calls, or filter to Email/Chat when they ask about those channels. When the user asks about "contacts"/"interactions" generally, do not restrict media_type
- For Zendesk ticket channel volume (email/chat/web/phone counts), use analytics_zendesk_ticket_channels — NOT analytics_zendesk_tickets. Bridge tickets (Phone Call form / "agent created" tag) link a call to a parent ticket and must be excluded from channel counts to avoid double-counting voice
- Zendesk via_channel values: voice=phone, mail=email, web=web form, chat=chat. "agent created" tag marks a phone bridge ticket (stored as voice but excluded from channel counts)
- Default to inbound PhoneCall when the user asks about "calls" without specifying a channel or direction
- Prefer link_method = 'call_object_to_parent' for ticket-enriched analysis unless user wants all segments
- For transcript-only LLM reasons (primary/secondary/tertiary), use analytics_transcript_summaries — not call_reason from Zendesk
- For reason -> outcome questions (resolution, escalations, repeat callers): use analytics_reason_outcomes for per-reason rates, or analytics_interaction_outcomes to slice by other dimensions
- Resolution = ticket_status solved/closed; unresolved = new/open/pending/hold. Escalation is a heuristic (high/urgent priority OR a ticket tag containing "escalat")
- Repeat contacts are keyed on contact_no (caller phone), NOT contact_id (contact_id is unique per call and never identifies a repeat customer)
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

Call volume grouped by ticket form type last 7 days:
SELECT ticket_form_name, COUNT(*) AS call_count
FROM analytics_interactions
WHERE interaction_start >= NOW() - INTERVAL '7 days'
  AND ticket_form_name IS NOT NULL
GROUP BY ticket_form_name
ORDER BY call_count DESC
LIMIT 20;

Zendesk ticket volume by channel last 7 days (deduped — excludes phone bridge tickets):
SELECT via_channel, COUNT(*) AS ticket_count
FROM analytics_zendesk_ticket_channels
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY via_channel
ORDER BY ticket_count DESC
LIMIT 20;

Top call reasons for specific form types:
SELECT call_reason, COUNT(*) AS n
FROM analytics_interactions
WHERE ticket_form_name IN ('Assist (Internal)')
  AND call_reason IS NOT NULL
GROUP BY call_reason
ORDER BY n DESC
LIMIT 20;

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

Top reasons driving contacts and what to do about them (latest reduction report):
SELECT rank, primary_reason, call_count, share_pct, recommendation_source, recommendations_text
FROM analytics_reduction_recommendations
ORDER BY rank
LIMIT 10;

Recommendations for a specific reason (latest reduction report):
SELECT primary_reason, call_count, share_pct, recommendations_text, reduction_hints
FROM analytics_reduction_recommendations
WHERE primary_reason ILIKE '%order status%'
ORDER BY rank
LIMIT 5;

Reasons with the worst outcomes (high volume that escalates or repeats):
SELECT call_reason, call_count, escalated_pct, repeat_contact_pct, unresolved_pct
FROM analytics_reason_outcomes
ORDER BY call_count DESC
LIMIT 20;

Reasons most likely to escalate (min volume to be meaningful):
SELECT call_reason, call_count, escalated_count, escalated_pct
FROM analytics_reason_outcomes
WHERE call_count >= 20
ORDER BY escalated_pct DESC
LIMIT 15;

Reasons that generate the most repeat callers:
SELECT call_reason, call_count, repeat_contact_count, repeat_contact_pct
FROM analytics_reason_outcomes
WHERE call_count >= 20
ORDER BY repeat_contact_pct DESC
LIMIT 15;

Resolution rate by transcript primary reason last 30 days (inbound):
SELECT primary_reason,
       COUNT(*) AS call_count,
       COUNT(*) FILTER (WHERE is_resolved) AS resolved,
       ROUND(100.0 * COUNT(*) FILTER (WHERE is_resolved) / COUNT(*), 1) AS resolved_pct
FROM analytics_interaction_outcomes
WHERE interaction_start >= NOW() - INTERVAL '30 days'
  AND upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
  AND primary_reason IS NOT NULL
GROUP BY primary_reason
ORDER BY call_count DESC
LIMIT 20;

Top reasons by canonical category (consolidated — preferred for rankings):
SELECT canonical_reason, call_count, escalated_pct, repeat_contact_pct, unresolved_pct
FROM analytics_canonical_reason_outcomes
ORDER BY call_count DESC
LIMIT 15;

Reasons where agent tagging disagrees most with the call transcript (min volume):
SELECT call_reason_canonical, comparable_calls, agree_pct, disagree_pct
FROM analytics_reason_reconciliation
WHERE comparable_calls >= 20
ORDER BY disagree_pct DESC
LIMIT 15;

Specific mis-tagged tickets to review (confident mismatches, newest first):
SELECT segment_id, ticket_id, interaction_start, skill_name,
       tagged_reason_canonical, transcript_reason_canonical, ticket_status
FROM analytics_reason_mismatches
WHERE tagged_reason_canonical <> 'Other / Uncategorized'
  AND transcript_reason_canonical <> 'Other / Uncategorized'
ORDER BY interaction_start DESC
LIMIT 25;

Most common mis-tag pairs (what agents tag vs what the call was about):
SELECT tagged_reason_canonical, transcript_reason_canonical, COUNT(*) AS n
FROM analytics_reason_mismatches
WHERE tagged_reason_canonical <> 'Other / Uncategorized'
  AND transcript_reason_canonical <> 'Other / Uncategorized'
GROUP BY tagged_reason_canonical, transcript_reason_canonical
ORDER BY n DESC
LIMIT 20;

Example repeat-contact calls for a reason (drill-down):
SELECT segment_id, interaction_start, contact_no, contact_interaction_count,
       prior_contacts_30d, ticket_status, resolution_status
FROM analytics_interaction_outcomes
WHERE call_reason ILIKE '%order status%'
  AND is_repeat_contact
ORDER BY contact_interaction_count DESC, interaction_start DESC
LIMIT 20;

Period-over-period comparison — inbound call volume this week vs last week (trend/compare):
WITH compare AS (
    SELECT
        COUNT(*) FILTER (WHERE interaction_start >= NOW() - INTERVAL '7 days') AS current_count,
        COUNT(*) FILTER (
            WHERE interaction_start >= NOW() - INTERVAL '14 days'
              AND interaction_start < NOW() - INTERVAL '7 days'
        ) AS prior_count
    FROM analytics_interactions
    WHERE upper(replace(call_direction, '-', '_')) LIKE '%IN_BOUND%'
)
SELECT current_count, prior_count, current_count - prior_count AS change,
       ROUND(100.0 * (current_count - prior_count) / NULLIF(prior_count, 0), 1) AS pct_change
FROM compare;

Period-over-period by canonical reason (which reasons grew the most, last 7 days vs prior 7):
SELECT call_reason_canonical,
       COUNT(*) FILTER (WHERE interaction_start >= NOW() - INTERVAL '7 days') AS current_count,
       COUNT(*) FILTER (
           WHERE interaction_start >= NOW() - INTERVAL '14 days'
             AND interaction_start < NOW() - INTERVAL '7 days'
       ) AS prior_count
FROM analytics_interactions
WHERE call_reason_canonical IS NOT NULL
GROUP BY call_reason_canonical
ORDER BY (
    COUNT(*) FILTER (WHERE interaction_start >= NOW() - INTERVAL '7 days')
    - COUNT(*) FILTER (
        WHERE interaction_start >= NOW() - INTERVAL '14 days'
          AND interaction_start < NOW() - INTERVAL '7 days'
    )
) DESC
LIMIT 20;

Drill-down — the actual calls behind a reason (individual rows, not an aggregate):
SELECT segment_id, interaction_start, skill_name, primary_reason, primary_reason_canonical,
       transcript_summary
FROM analytics_transcript_summaries
WHERE primary_reason_canonical = 'Remake / replacement'
  AND interaction_start >= NOW() - INTERVAL '7 days'
ORDER BY interaction_start DESC
LIMIT 25;
""".strip()


def build_schema_prompt() -> str:
    return SCHEMA_CONTEXT
