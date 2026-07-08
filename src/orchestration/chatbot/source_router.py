"""Deterministic data-source router for the analytics agent.

The ReAct planner tends to default to ``analytics_interactions`` (CXone phone calls linked to
Zendesk parent tickets). That view excludes standalone email/chat/web tickets, so questions about
the broader ticket population were being answered from the narrow call slice.

This module classifies each question up front and returns a strong routing directive that is
injected into the planner payload, so source selection is deterministic rather than left to the
model's discretion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Explicit phone / call language → CXone call view (analytics_interactions).
CALL_PATTERNS = re.compile(
    r"\b("
    r"calls?|called|calling|callers?|phone|phone[\s-]?calls?|voice|transcripts?|"
    r"spoke|speaking|said on the|on the call|ivr|acd|agent said|hold time|talk time|"
    r"call reasons?|inbound calls?|outbound calls?"
    r")\b",
    re.IGNORECASE,
)

# Explicit ticket / non-phone channel language → Zendesk ticket views.
TICKET_PATTERNS = re.compile(
    r"\b("
    r"tickets?|emails?|e-?mails?|chats?|web ?form|web|messages?|messaging|"
    r"cases?|correspondence|written|by channel|per channel|across channels?|"
    r"all channels?|every channel|non[\s-]?phone|native[\s-]?messaging|"
    r"side[\s-]?conversation|form types?|ticket forms?"
    r")\b",
    re.IGNORECASE,
)

# Generic "all contacts" language → not phone-specific; prefer the broader ticket population.
ALL_CONTACT_PATTERNS = re.compile(
    r"\b("
    r"contacts?|interactions?|volume|tickets? or calls|calls? or tickets|"
    r"overall|in total|altogether|combined|everything"
    r")\b",
    re.IGNORECASE,
)

CALL_DIRECTIVE = (
    "ROUTING DIRECTIVE (mandatory): This question is about PHONE CALLS. Use "
    "run_analytics_sql on analytics_interactions (call intents) and, for qualitative "
    "examples, search_knowledge with source_type=call_interaction."
)

TICKET_DIRECTIVE = (
    "ROUTING DIRECTIVE (mandatory): This question is about ZENDESK TICKETS / non-phone "
    "channels (email, chat, web). Use run_analytics_sql ticket intents "
    "(ticket_channels, top_ticket_volume_by_form, ticket_count_by_dimension, ticket_drilldown) "
    "which read analytics_zendesk_ticket_channels, and search_knowledge with "
    "source_type=zendesk_ticket for qualitative examples. Do NOT use analytics_interactions "
    "— it only contains phone calls and will undercount tickets."
)

MIXED_DIRECTIVE = (
    "ROUTING DIRECTIVE (mandatory): This question references BOTH calls and tickets. Query "
    "both sources: analytics_interactions for phone calls AND analytics_zendesk_ticket_channels "
    "for the full ticket population, then reconcile. Use search_knowledge with source_type=all "
    "for qualitative examples."
)

ALL_CHANNEL_DIRECTIVE = (
    "ROUTING DIRECTIVE (mandatory): This question is NOT phone-specific — it is about overall "
    "contacts/interactions across all channels. Prefer analytics_zendesk_ticket_channels (all "
    "email/chat/web/phone tickets, deduped) for volume, and search_knowledge with source_type=all "
    "for qualitative examples. Only fall back to analytics_interactions if you specifically need "
    "CXone call transcript detail; do NOT treat analytics_interactions as the whole picture."
)


@dataclass(frozen=True)
class SourceRoute:
    source: str  # one of: call, ticket, mixed, all_channel
    directive: str


def classify_question_source(question: str) -> SourceRoute:
    """Classify a question's target data source and return a mandatory routing directive."""
    text = question or ""
    has_call = bool(CALL_PATTERNS.search(text))
    has_ticket = bool(TICKET_PATTERNS.search(text))

    if has_call and has_ticket:
        return SourceRoute("mixed", MIXED_DIRECTIVE)
    if has_ticket:
        return SourceRoute("ticket", TICKET_DIRECTIVE)
    if has_call:
        return SourceRoute("call", CALL_DIRECTIVE)
    if ALL_CONTACT_PATTERNS.search(text):
        return SourceRoute("all_channel", ALL_CHANNEL_DIRECTIVE)
    # No strong signal: default to the broader all-channel guidance so the agent stops
    # silently narrowing to phone calls.
    return SourceRoute("all_channel", ALL_CHANNEL_DIRECTIVE)
