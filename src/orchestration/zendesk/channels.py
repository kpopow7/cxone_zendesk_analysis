"""Zendesk ticket channel resolution.

Tickets tagged ``agent created`` are phone calls created by agents (regardless of the
raw ``via.channel`` on the ticket). All other tickets keep the channel Zendesk reports
(e.g. ``mail`` for email, ``web``, ``chat``, ``voice``).

Phone-call **bridge** tickets (``agent created`` tag and/or Phone Call form type) link a
CXone call to a parent ticket. They must be excluded from channel volume counts so voice
is not double-counted alongside the parent ticket.
"""

from __future__ import annotations

# Zendesk uses ``voice`` for phone-call tickets.
AGENT_CREATED_PHONE_CHANNEL = "voice"
AGENT_CREATED_TAG = "agent created"

# SQL expression (Postgres) shared by analytics views — keep in sync with Python helpers.
AGENT_CREATED_TAG_SQL = "lower(trim(replace(tag, '_', ' '))) = 'agent created'"

IS_PHONE_BRIDGE_TICKET_SQL = f"""(
    EXISTS (
        SELECT 1
        FROM jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(z.tags) = 'array' THEN z.tags ELSE '[]'::jsonb END
        ) AS tag
        WHERE {AGENT_CREATED_TAG_SQL}
    )
    OR lower(coalesce(f.name, '')) LIKE '%phone call%'
)"""


def _normalize_tag(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").split())


def has_agent_created_tag(tags: list[str] | None) -> bool:
    if not tags:
        return False
    target = _normalize_tag(AGENT_CREATED_TAG)
    return any(_normalize_tag(str(tag)) == target for tag in tags if tag is not None)


def is_phone_bridge_ticket(
    *,
    tags: list[str] | None,
    ticket_form_id: int | None = None,
    ticket_form_name: str | None = None,
    phone_call_form_ids: frozenset[int] | None = None,
) -> bool:
    """True for CXone phone-call bridge tickets (same rows as Phone Call form / agent created)."""
    if has_agent_created_tag(tags):
        return True
    if phone_call_form_ids and ticket_form_id is not None:
        if int(ticket_form_id) in phone_call_form_ids:
            return True
    if ticket_form_name and "phone call" in ticket_form_name.strip().lower():
        return True
    return False


def resolve_ticket_via_channel(
    *,
    tags: list[str] | None,
    raw_via_channel: str | None,
) -> str | None:
    """Return the effective ticket channel for storage and analytics."""
    if has_agent_created_tag(tags):
        return AGENT_CREATED_PHONE_CHANNEL
    if raw_via_channel is None:
        return None
    text = str(raw_via_channel).strip()
    return text or None
