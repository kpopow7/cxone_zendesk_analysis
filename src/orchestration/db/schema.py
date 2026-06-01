from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CxoneTranscriptRow(Base):
    __tablename__ = "cxone_transcripts"

    segment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    segment_contact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acd_contact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acd_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interaction_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interaction_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ticket_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    client_sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    segment_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CxoneTranscriptAnalysisRow(Base):
    """LLM-derived call reason hierarchy from cxone_transcripts (transcript-only analysis)."""

    __tablename__ = "cxone_transcript_analysis"

    segment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    transcript_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    primary_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    secondary_reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    tertiary_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reduction_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ZendeskTicketRow(Base):
    __tablename__ = "zendesk_tickets"
    # Promoted custom-field columns (cf_*) are attached below from config/zendesk_field_map.json

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticket_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requester_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    submitter_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    organization_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    brand_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ticket_form_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    via_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_public: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_incidents: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    custom_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    promoted_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    raw_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    row_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CombinedInteractionRow(Base):
    """CXone segment joined to Zendesk ticket for analysis and summarization."""

    __tablename__ = "combined_interactions"

    segment_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )  # parent / detail ticket
    phone_call_ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    link_method: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    link_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parent_link_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    segment_contact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    acd_contact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acd_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    interaction_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    interaction_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    skill_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent_sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    segment_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ticket_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ticket_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ticket_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ticket_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ticket_priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticket_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ticket_tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    ticket_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticket_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ticket_via_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ticket_form_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    zendesk_promoted_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    zendesk_phone_call_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    call_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_reason_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    disposition_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    disposition_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cxone_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    zendesk_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ZendeskTicketCommentRow(Base):
    __tablename__ = "zendesk_ticket_comments"

    comment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_public: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    via_channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    plain_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    row_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


from orchestration.zendesk.promoted_columns import (  # noqa: E402
    attach_promoted_columns,
    ensure_promoted_columns,
)

attach_promoted_columns(ZendeskTicketRow)


def ensure_transcript_analysis_table(database_url: str) -> None:
    """Create cxone_transcript_analysis if missing (safe to call before Step 4b)."""
    from orchestration.db.session import get_engine

    CxoneTranscriptAnalysisRow.__table__.create(get_engine(database_url), checkfirst=True)


def init_database(database_url: str) -> None:
    from orchestration.db.session import get_engine
    from orchestration.linking.combined_columns import ensure_combined_interaction_columns

    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    ensure_promoted_columns(engine)
    ensure_combined_interaction_columns(engine)
    CxoneTranscriptAnalysisRow.__table__.create(engine, checkfirst=True)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
