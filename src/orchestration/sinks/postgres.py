from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from orchestration.config import Settings
from orchestration.db.schema import CxoneTranscriptRow, init_database, utc_now
from orchestration.db.session import get_session_factory
from orchestration.models import TranscriptRecord


class PostgresTranscriptSink:
    """Persist transcript records to local (or remote) PostgreSQL."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_url = settings.database_url
        init_database(self._database_url)
        self._session_factory = get_session_factory(self._database_url)

    def upsert_records(self, records: list[TranscriptRecord]) -> dict[str, int]:
        if not records:
            return {"upserted": 0}

        extracted_at = utc_now()
        rows = [_record_to_row(record, extracted_at) for record in records]

        with self._session_factory() as session:
            for row in rows:
                stmt = insert(CxoneTranscriptRow).values(**row)
                excluded = stmt.excluded
                update_columns = {
                    column.name: getattr(excluded, column.name)
                    for column in CxoneTranscriptRow.__table__.columns
                    if column.name not in ("segment_id", "created_at")
                }
                update_columns["updated_at"] = utc_now()
                stmt = stmt.on_conflict_do_update(
                    index_elements=[CxoneTranscriptRow.segment_id],
                    set_=update_columns,
                )
                session.execute(stmt)
            session.commit()

        return {"upserted": len(records)}


def _record_to_row(record: TranscriptRecord, extracted_at) -> dict:
    return {
        "segment_id": record.segment_id,
        "segment_contact_id": record.segment_contact_id,
        "contact_id": record.contact_id,
        "acd_contact_id": record.acd_contact_id,
        "acd_session_id": record.acd_session_id,
        "contact_no": record.contact_no,
        "interaction_start": record.interaction_start,
        "interaction_end": record.interaction_end,
        "agent_name": record.agent_name,
        "team_name": record.team_name,
        "skill_name": record.skill_name,
        "ticket_id": record.ticket_id,
        "media_type": record.media_type,
        "call_direction": record.call_direction,
        "language_code": record.language_code,
        "client_sentiment": record.client_sentiment,
        "agent_sentiment": record.agent_sentiment,
        "segment_summary": record.segment_summary,
        "transcript_text": record.transcript_text or "",
        "raw_metadata": record.raw_metadata or {},
        "extracted_at": extracted_at,
    }
