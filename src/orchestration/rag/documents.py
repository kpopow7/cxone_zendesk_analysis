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
