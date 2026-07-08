from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class KnowledgeDocument:
    chunk_id: str
    source_type: str
    source_id: str
    interaction_start: datetime | None
    skill_name: str | None
    primary_reason: str | None
    secondary_reason: str | None
    content: str
    metadata: dict[str, Any]

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _clean(value: object | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _append_section(lines: list[str], title: str, value: object | None) -> None:
    cleaned = _clean(value)
    if cleaned:
        lines.append(f"{title}: {cleaned}")


def build_call_interaction_document(row: dict[str, Any]) -> KnowledgeDocument | None:
    """Build one searchable narrative document for a call (transcript + Zendesk context)."""
    segment_id = _clean(row.get("segment_id"))
    if not segment_id:
        return None

    lines: list[str] = ["Contact center call interaction"]
    _append_section(lines, "Segment ID", segment_id)
    _append_section(lines, "Call time", row.get("interaction_start"))
    _append_section(lines, "Direction", row.get("call_direction"))
    _append_section(lines, "Media", row.get("media_type"))
    _append_section(lines, "Skill", row.get("skill_name"))
    _append_section(lines, "Team", row.get("team_name"))
    _append_section(lines, "Agent", row.get("agent_name"))
    _append_section(lines, "Client sentiment", row.get("client_sentiment"))

    _append_section(lines, "Transcript primary reason", row.get("primary_reason"))
    _append_section(lines, "Transcript secondary reason", row.get("secondary_reason"))
    _append_section(lines, "Transcript tertiary reason", row.get("tertiary_reason"))
    _append_section(lines, "Call summary", row.get("transcript_summary"))
    _append_section(lines, "Reduction hint", row.get("reduction_hint"))

    _append_section(lines, "Zendesk ticket ID", row.get("ticket_id"))
    _append_section(lines, "Ticket subject", row.get("ticket_subject"))
    _append_section(lines, "Ticket status", row.get("ticket_status"))
    _append_section(lines, "Ticket priority", row.get("ticket_priority"))
    _append_section(lines, "Zendesk call reason", row.get("call_reason"))
    _append_section(lines, "Zendesk disposition", row.get("disposition_label"))
    _append_section(lines, "CXone segment summary", row.get("segment_summary"))

    preview = _clean(row.get("transcript_preview"))
    if preview:
        lines.append(f"Transcript excerpt: {preview[:1800]}")

    ticket_description = _clean(row.get("ticket_description"))
    if ticket_description:
        lines.append(f"Ticket description: {ticket_description[:800]}")

    content = "\n".join(lines)
    if len(content) < 80:
        return None

    metadata = {
        "segment_id": segment_id,
        "interaction_start": _iso_or_none(row.get("interaction_start")),
        "skill_name": _clean(row.get("skill_name")),
        "primary_reason": _clean(row.get("primary_reason")),
        "secondary_reason": _clean(row.get("secondary_reason")),
        "tertiary_reason": _clean(row.get("tertiary_reason")),
        "ticket_id": row.get("ticket_id"),
        "call_reason": _clean(row.get("call_reason")),
        "disposition_label": _clean(row.get("disposition_label")),
    }

    return KnowledgeDocument(
        chunk_id=segment_id,
        source_type="call_interaction",
        source_id=segment_id,
        interaction_start=_parse_datetime(row.get("interaction_start")),
        skill_name=_clean(row.get("skill_name")),
        primary_reason=_clean(row.get("primary_reason")),
        secondary_reason=_clean(row.get("secondary_reason")),
        content=content,
        metadata=metadata,
    )


def build_zendesk_ticket_document(row: dict[str, Any]) -> KnowledgeDocument | None:
    """Build one searchable narrative document for a parent/detail Zendesk ticket."""
    ticket_id = row.get("ticket_id")
    if ticket_id is None:
        return None
    ticket_id_str = str(int(ticket_id))

    lines: list[str] = ["Zendesk support ticket"]
    _append_section(lines, "Ticket ID", ticket_id_str)
    _append_section(lines, "Created", row.get("created_at"))
    _append_section(lines, "Channel", row.get("via_channel"))
    _append_section(lines, "Form type", row.get("ticket_form_name"))
    _append_section(lines, "Status", row.get("status"))
    _append_section(lines, "Priority", row.get("priority"))
    _append_section(lines, "Subject", row.get("subject"))

    description = _clean(row.get("description_preview") or row.get("description"))
    if description:
        lines.append(f"Description: {description[:1200]}")

    tags = row.get("tags")
    if isinstance(tags, list) and tags:
        tag_text = ", ".join(str(tag) for tag in tags if tag)
        if tag_text:
            lines.append(f"Tags: {tag_text[:500]}")

    promoted = row.get("promoted_fields")
    if isinstance(promoted, dict):
        for key in sorted(promoted):
            value = _clean(promoted.get(key))
            if value:
                label = str(key).replace("cf_", "").replace("_", " ").strip().title()
                lines.append(f"{label}: {value[:400]}")

    content = "\n".join(lines)
    if len(content) < 40:
        return None

    call_reason = None
    if isinstance(promoted, dict):
        for key in (
            "cf_reason_for_contact_consumer",
            "cf_reason_for_contact_installerdealer",
            "cf_reason_for_contact_customer_levolor",
            "cf_i_need_help_with",
            "cf_intent",
        ):
            call_reason = _clean(promoted.get(key))
            if call_reason:
                break

    metadata = {
        "ticket_id": int(ticket_id),
        "created_at": _iso_or_none(row.get("created_at")),
        "via_channel": _clean(row.get("via_channel")),
        "ticket_form_name": _clean(row.get("ticket_form_name")),
        "status": _clean(row.get("status")),
        "call_reason": call_reason,
    }

    return KnowledgeDocument(
        chunk_id=f"zendesk:{ticket_id_str}",
        source_type="zendesk_ticket",
        source_id=ticket_id_str,
        interaction_start=_parse_datetime(row.get("created_at")),
        skill_name=None,
        primary_reason=call_reason,
        secondary_reason=None,
        content=content,
        metadata=metadata,
    )


def _iso_or_none(value: object | None) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else None


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def metadata_json(document: KnowledgeDocument) -> str:
    return json.dumps(document.metadata, default=str)
