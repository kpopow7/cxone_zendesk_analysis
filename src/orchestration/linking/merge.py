from __future__ import annotations

from orchestration.db.schema import CxoneTranscriptRow, ZendeskTicketRow
from orchestration.linking.matcher import ResolvedLink
from orchestration.models import CombinedInteractionRecord
from orchestration.zendesk.promoted_columns import PROMOTED_COLUMN_NAMES


def build_combined_record(
    cxone: CxoneTranscriptRow,
    *,
    detail_ticket: ZendeskTicketRow | None,
    phone_call_ticket: ZendeskTicketRow | None,
    resolved: ResolvedLink | None,
) -> CombinedInteractionRecord:
    promoted_snapshot = _promoted_snapshot(detail_ticket) if detail_ticket is not None else {}
    phone_call_snapshot = (
        _promoted_snapshot(phone_call_ticket) if phone_call_ticket is not None else {}
    )

    return CombinedInteractionRecord(
        segment_id=cxone.segment_id,
        ticket_id=int(detail_ticket.ticket_id) if detail_ticket is not None else None,
        phone_call_ticket_id=(
            int(phone_call_ticket.ticket_id) if phone_call_ticket is not None else None
        ),
        link_method=resolved.link_method if resolved else "unmatched",
        link_key=resolved.link_key if resolved else None,
        parent_link_key=resolved.parent_link_key if resolved else None,
        segment_contact_id=cxone.segment_contact_id,
        contact_id=cxone.contact_id,
        acd_contact_id=cxone.acd_contact_id,
        acd_session_id=cxone.acd_session_id,
        contact_no=cxone.contact_no,
        interaction_start=cxone.interaction_start,
        interaction_end=cxone.interaction_end,
        call_direction=cxone.call_direction,
        media_type=cxone.media_type,
        agent_name=cxone.agent_name,
        team_name=cxone.team_name,
        skill_name=cxone.skill_name,
        client_sentiment=cxone.client_sentiment,
        agent_sentiment=cxone.agent_sentiment,
        segment_summary=cxone.segment_summary,
        transcript_text=cxone.transcript_text or "",
        ticket_url=detail_ticket.url if detail_ticket else None,
        ticket_external_id=detail_ticket.external_id if detail_ticket else None,
        ticket_subject=detail_ticket.subject if detail_ticket else None,
        ticket_description=detail_ticket.description if detail_ticket else None,
        ticket_status=detail_ticket.status if detail_ticket else None,
        ticket_priority=detail_ticket.priority if detail_ticket else None,
        ticket_type=detail_ticket.ticket_type if detail_ticket else None,
        ticket_tags=list(detail_ticket.tags) if detail_ticket and detail_ticket.tags else [],
        ticket_created_at=detail_ticket.created_at if detail_ticket else None,
        ticket_updated_at=detail_ticket.updated_at if detail_ticket else None,
        ticket_via_channel=detail_ticket.via_channel if detail_ticket else None,
        ticket_form_id=int(detail_ticket.ticket_form_id) if detail_ticket and detail_ticket.ticket_form_id else None,
        zendesk_promoted_fields=promoted_snapshot,
        zendesk_phone_call_fields=phone_call_snapshot,
        cxone_extracted_at=cxone.extracted_at,
        zendesk_extracted_at=detail_ticket.extracted_at if detail_ticket else None,
    )


def _promoted_snapshot(ticket: ZendeskTicketRow) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for column_name in PROMOTED_COLUMN_NAMES:
        value = getattr(ticket, column_name, None)
        if value is not None and str(value).strip():
            snapshot[column_name] = str(value).strip()
    promoted = ticket.promoted_fields
    if isinstance(promoted, dict):
        for key, value in promoted.items():
            if value is not None and str(value).strip():
                snapshot[str(key)] = str(value).strip()
    return snapshot
