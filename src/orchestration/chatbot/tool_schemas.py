"""OpenAI function-calling schemas for the ReAct agent."""

from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "resolve_entities",
            "description": (
                "Resolve fuzzy user mentions (e.g. 'assist', 'remake', 'HD Brite') to exact "
                "database values before querying. Call this first when the question references "
                "a ticket form type, skill, or reason category."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "form_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fuzzy form type mentions, e.g. ['assist']",
                    },
                    "skill_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fuzzy skill/queue mentions",
                    },
                    "reason_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fuzzy reason mentions, e.g. ['remake', 'order status']",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_catalog",
            "description": (
                "List available values for a dimension when unsure of exact spelling "
                "(form_types, skills, media_types, canonical_reasons)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "description": "One of: form_types, skills, media_types, canonical_reasons",
                    },
                    "limit": {"type": "integer", "description": "Max values to return (default 50)"},
                },
                "required": ["dimension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_analytics_sql",
            "description": (
                "Run a structured analytics query against PostgreSQL views. Prefer vetted intents "
                "over raw SQL. Call intents: top_reasons, top_transcript_reasons, count_by_dimension, "
                "drilldown, trend_compare. Ticket intents: ticket_channels, top_ticket_volume_by_form, "
                "ticket_drilldown, ticket_count_by_dimension."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "Vetted intent name (preferred)",
                    },
                    "sql": {
                        "type": "string",
                        "description": "Custom SELECT only if no intent fits",
                    },
                    "form_names": {"type": "array", "items": {"type": "string"}},
                    "skill_names": {"type": "array", "items": {"type": "string"}},
                    "canonical_reasons": {"type": "array", "items": {"type": "string"}},
                    "media_types": {"type": "array", "items": {"type": "string"}},
                    "days": {
                        "type": "integer",
                        "description": "Trailing day window on interaction_start (e.g. 7 for last week)",
                    },
                    "limit": {"type": "integer", "description": "Max rows (default 20)"},
                    "inbound_only": {
                        "type": "boolean",
                        "description": "Restrict to inbound interactions (default true for 'calls')",
                    },
                    "dimension": {
                        "type": "string",
                        "description": "For count_by_dimension: skill_name, ticket_form_name, etc.",
                    },
                    "reason_filter": {
                        "type": "string",
                        "description": "For drilldown: partial reason match when canonical list unknown",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Semantic search over indexed call transcripts AND Zendesk tickets. Use for "
                "qualitative 'why' questions, patterns, and example tickets/calls. Prefer "
                "source_type=zendesk_ticket for email/chat/web ticket questions; "
                "source_type=call_interaction for phone call narratives; source_type=all when unsure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Search query (defaults to the user's question if omitted)",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["all", "call_interaction", "zendesk_ticket"],
                        "description": "Which knowledge source to search (default all)",
                    },
                    "skill_name": {"type": "string"},
                    "canonical_reason": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_interactions",
            "description": (
                "Semantic search over phone call summaries only (subset of search_knowledge). "
                "Prefer search_knowledge unless you specifically need call-only examples."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Search query (defaults to the user's question if omitted)",
                    },
                    "skill_name": {"type": "string"},
                    "canonical_reason": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reduction_recommendations",
            "description": (
                "Fetch ranked recommendations for how to reduce contact volume from the latest "
                "reduction report. Optionally filter by primary_reason names from a prior query."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasons": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Reason names to look up (from top_reasons query)",
                    },
                    "limit": {"type": "integer"},
                },
            },
        },
    },
]

PLANNER_SYSTEM_PROMPT = """You are a contact-center analytics agent with tools to query PostgreSQL and semantic search.

Data source routing (critical — do not default everything to analytics_interactions):
- Phone calls linked to Zendesk parent tickets → run_analytics_sql on analytics_interactions
  (intents: top_reasons, count_by_dimension, trend_compare, drilldown)
- All Zendesk tickets (email, chat, web, phone parents) → run_analytics_sql ticket intents:
  ticket_channels, top_ticket_volume_by_form, ticket_drilldown, ticket_count_by_dimension
  (these use analytics_zendesk_ticket_channels — bridge tickets excluded)
- Qualitative examples / "why" on phone calls → search_knowledge with source_type=call_interaction
- Qualitative examples on email/chat/web tickets → search_knowledge with source_type=zendesk_ticket
- General semantic search when unsure → search_knowledge with source_type=all
- Do NOT use analytics_interactions for email/chat/web ticket volume or ticket-only questions

Workflow:
1. If the user mentions a form type, skill, or reason category, call resolve_entities first.
2. Pick the correct data source using the routing rules above.
3. Use run_analytics_sql with vetted intents for counts, rankings, trends, drill-downs.
4. Use get_reduction_recommendations when the user asks what to do / how to reduce volume.
5. Use search_knowledge for qualitative 'why' questions or example tickets/calls.
6. If a query returns 0 rows, try list_catalog or broaden filters (widen date range, try search_knowledge).
7. When you have enough data, stop calling tools and respond with a short plan summary in plain text.

Rules:
- Never invent numbers; only use tool results.
- Prefer call_reason_canonical / primary_reason_canonical for call rankings.
- For Zendesk channel counts use ticket_channels intent (deduped — no phone-bridge double count).
- Reduction recommendations are phone-wide, not per ticket form — note that when relevant.
- UI form-type filters (if active) are mandatory — use those exact form_names in SQL tools.
"""
