from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import insert

from orchestration.config import Settings
from orchestration.db.schema import CombinedInteractionRow, init_database, utc_now
from orchestration.db.session import get_engine, get_session_factory
from orchestration.linking.combined_columns import ensure_combined_interaction_columns
from orchestration.models import CombinedInteractionRecord

UPSERT_BATCH_SIZE = 50


class PostgresCombinedSink:
    """Persist combined CXone + Zendesk rows for analysis."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_url = settings.database_url
        init_database(self._database_url)
        ensure_combined_interaction_columns(get_engine(self._database_url))
        self._session_factory = get_session_factory(self._database_url)

    def upsert_records(self, records: list[CombinedInteractionRecord]) -> dict[str, int]:
        if not records:
            return {"upserted": 0}

        built_at = utc_now()
        upserted = 0

        with self._session_factory() as session:
            for offset in range(0, len(records), UPSERT_BATCH_SIZE):
                batch = records[offset : offset + UPSERT_BATCH_SIZE]
                rows = [_record_to_row(record, built_at) for record in batch]
                upserted += _upsert_batch(session, rows)

        return {"upserted": upserted}


def _upsert_batch(session, rows: list[dict]) -> int:
    if not rows:
        return 0

    pk_names = [key.name for key in inspect(CombinedInteractionRow).primary_key]
    stmt = insert(CombinedInteractionRow).values(rows)
    update_columns = {
        col.name: stmt.excluded[col.name]
        for col in CombinedInteractionRow.__table__.columns
        if col.name not in pk_names and col.name != "created_at"
    }
    update_columns["updated_at"] = utc_now()
    stmt = stmt.on_conflict_do_update(
        index_elements=pk_names,
        set_=update_columns,
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


def _record_to_row(record: CombinedInteractionRecord, built_at) -> dict:
    return {
        "segment_id": record.segment_id,
        "ticket_id": record.ticket_id,
        "phone_call_ticket_id": record.phone_call_ticket_id,
        "link_method": record.link_method,
        "link_key": record.link_key,
        "parent_link_key": record.parent_link_key,
        "segment_contact_id": record.segment_contact_id,
        "contact_id": record.contact_id,
        "acd_contact_id": record.acd_contact_id,
        "acd_session_id": record.acd_session_id,
        "contact_no": record.contact_no,
        "interaction_start": record.interaction_start,
        "interaction_end": record.interaction_end,
        "call_direction": record.call_direction,
        "media_type": record.media_type,
        "agent_name": record.agent_name,
        "team_name": record.team_name,
        "skill_name": record.skill_name,
        "client_sentiment": record.client_sentiment,
        "agent_sentiment": record.agent_sentiment,
        "segment_summary": record.segment_summary,
        "transcript_text": record.transcript_text,
        "ticket_url": record.ticket_url,
        "ticket_external_id": record.ticket_external_id,
        "ticket_subject": record.ticket_subject,
        "ticket_description": record.ticket_description,
        "ticket_status": record.ticket_status,
        "ticket_priority": record.ticket_priority,
        "ticket_type": record.ticket_type,
        "ticket_tags": record.ticket_tags or [],
        "ticket_created_at": record.ticket_created_at,
        "ticket_updated_at": record.ticket_updated_at,
        "ticket_via_channel": record.ticket_via_channel,
        "ticket_form_id": record.ticket_form_id,
        "zendesk_promoted_fields": record.zendesk_promoted_fields or {},
        "zendesk_phone_call_fields": record.zendesk_phone_call_fields or {},
        "call_reason": record.call_reason,
        "call_reason_code": record.call_reason_code,
        "call_reason_source": record.call_reason_source,
        "disposition_code": record.disposition_code,
        "disposition_label": record.disposition_label,
        "disposition_source": record.disposition_source,
        "cxone_extracted_at": record.cxone_extracted_at,
        "zendesk_extracted_at": record.zendesk_extracted_at,
        "built_at": built_at,
    }
