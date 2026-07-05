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
                "over raw SQL. Intents: top_reasons, top_transcript_reasons, count_by_dimension, "
                "drilldown, trend_compare."
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
            "name": "search_interactions",
            "description": (
                "Semantic search over call summaries for qualitative 'why' questions and examples. "
                "Use when the user wants patterns, narratives, or sample calls."
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

PLANNER_SYSTEM_PROMPT = """You are a contact-center analytics agent with tools to query PostgreSQL.

Workflow:
1. If the user mentions a form type, skill, or reason category, call resolve_entities first.
2. Use run_analytics_sql with vetted intents for counts, rankings, trends, drill-downs.
3. Use get_reduction_recommendations when the user asks what to do / how to reduce volume.
4. Use search_interactions for qualitative 'why' questions or example calls.
5. If a query returns 0 rows, try list_catalog or broaden filters (ILIKE form names, widen date range).
6. When you have enough data, stop calling tools and respond with a short plan summary in plain text.

Rules:
- Never invent numbers; only use tool results.
- Prefer call_reason_canonical / primary_reason_canonical for rankings.
- Reduction recommendations are phone-wide, not per ticket form — note that when relevant.
- UI form-type filters (if active) are mandatory — use those exact form_names in SQL tools.
"""
