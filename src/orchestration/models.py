from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TranscriptRecord:
    """Normalized call transcript row for storage in PostgreSQL or other sinks."""

    segment_id: str
    segment_contact_id: str | None = None
    contact_id: str | None = None
    acd_contact_id: str | None = None
    acd_session_id: str | None = None
    contact_no: str | None = None
    interaction_start: datetime | None = None
    interaction_end: datetime | None = None
    agent_name: str | None = None
    team_name: str | None = None
    skill_name: str | None = None
    ticket_id: str | None = None
    media_type: str | None = None
    call_direction: str | None = None
    language_code: str | None = None
    client_sentiment: str | None = None
    agent_sentiment: str | None = None
    segment_summary: str | None = None
    transcript_text: str = ""
    raw_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TicketRecord:
    """Normalized Zendesk ticket row for storage in PostgreSQL."""

    ticket_id: int
    url: str | None = None
    external_id: str | None = None
    subject: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    ticket_type: str | None = None
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    due_at: datetime | None = None
    requester_id: int | None = None
    submitter_id: int | None = None
    assignee_id: int | None = None
    organization_id: int | None = None
    group_id: int | None = None
    brand_id: int | None = None
    ticket_form_id: int | None = None
    via_channel: str | None = None
    recipient: str | None = None
    is_public: bool | None = None
    has_incidents: bool | None = None
    custom_fields: dict = field(default_factory=dict)
    promoted_fields: dict = field(default_factory=dict)
    raw_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CombinedInteractionRecord:
    """CXone segment linked via phone-call ticket to parent Zendesk ticket (detail)."""

    segment_id: str
    link_method: str
    ticket_id: int | None = None
    phone_call_ticket_id: int | None = None
    link_key: str | None = None
    parent_link_key: str | None = None
    segment_contact_id: str | None = None
    contact_id: str | None = None
    acd_contact_id: str | None = None
    acd_session_id: str | None = None
    contact_no: str | None = None
    interaction_start: datetime | None = None
    interaction_end: datetime | None = None
    call_direction: str | None = None
    media_type: str | None = None
    agent_name: str | None = None
    team_name: str | None = None
    skill_name: str | None = None
    client_sentiment: str | None = None
    agent_sentiment: str | None = None
    segment_summary: str | None = None
    transcript_text: str = ""
    ticket_url: str | None = None
    ticket_external_id: str | None = None
    ticket_subject: str | None = None
    ticket_description: str | None = None
    ticket_status: str | None = None
    ticket_priority: str | None = None
    ticket_type: str | None = None
    ticket_tags: list[str] = field(default_factory=list)
    ticket_created_at: datetime | None = None
    ticket_updated_at: datetime | None = None
    ticket_via_channel: str | None = None
    ticket_form_id: int | None = None
    zendesk_promoted_fields: dict = field(default_factory=dict)
    zendesk_phone_call_fields: dict = field(default_factory=dict)
    call_reason: str | None = None
    call_reason_code: str | None = None
    call_reason_source: str | None = None
    disposition_code: str | None = None
    disposition_label: str | None = None
    disposition_source: str | None = None
    cxone_extracted_at: datetime | None = None
    zendesk_extracted_at: datetime | None = None


@dataclass(frozen=True)
class TicketCommentRecord:
    """Normalized Zendesk ticket comment row for storage in PostgreSQL."""

    comment_id: int
    ticket_id: int
    author_id: int | None = None
    created_at: datetime | None = None
    is_public: bool | None = None
    via_channel: str | None = None
    body: str | None = None
    html_body: str | None = None
    plain_body: str | None = None
    raw_metadata: dict = field(default_factory=dict)
