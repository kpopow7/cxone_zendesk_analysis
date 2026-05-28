from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from orchestration.config import Settings
from orchestration.db.schema import ZendeskTicketRow, init_database, utc_now
from orchestration.db.session import get_engine, get_session_factory
from orchestration.models import TicketRecord
from orchestration.zendesk.promoted_columns import (
    PROMOTED_COLUMN_NAMES,
    coerce_promoted_db_value,
    ensure_promoted_columns,
)


class PostgresZendeskSink:
    """Persist Zendesk ticket records to PostgreSQL."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_url = settings.database_url
        init_database(self._database_url)
        ensure_promoted_columns(get_engine(self._database_url))
        self._session_factory = get_session_factory(self._database_url)

    def upsert_records(self, records: list[TicketRecord]) -> dict[str, int]:
        if not records:
            return {"upserted": 0}

        extracted_at = utc_now()
        rows = [_record_to_row(record, extracted_at) for record in records]

        with self._session_factory() as session:
            for row in rows:
                stmt = insert(ZendeskTicketRow).values(**row)
                excluded = stmt.excluded
                update_columns = {
                    column.name: getattr(excluded, column.name)
                    for column in ZendeskTicketRow.__table__.columns
                    if column.name not in ("ticket_id", "row_created_at")
                }
                update_columns["row_updated_at"] = utc_now()
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ZendeskTicketRow.ticket_id],
                    set_=update_columns,
                )
                session.execute(stmt)
            session.commit()

        return {"upserted": len(records)}


def _record_to_row(record: TicketRecord, extracted_at) -> dict:
    row = {
        "ticket_id": record.ticket_id,
        "url": record.url,
        "external_id": record.external_id,
        "subject": record.subject,
        "description": record.description,
        "status": record.status,
        "priority": record.priority,
        "ticket_type": record.ticket_type,
        "tags": record.tags or [],
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "due_at": record.due_at,
        "requester_id": record.requester_id,
        "submitter_id": record.submitter_id,
        "assignee_id": record.assignee_id,
        "organization_id": record.organization_id,
        "group_id": record.group_id,
        "brand_id": record.brand_id,
        "ticket_form_id": record.ticket_form_id,
        "via_channel": record.via_channel,
        "recipient": record.recipient,
        "is_public": record.is_public,
        "has_incidents": record.has_incidents,
        "custom_fields": record.custom_fields or {},
        "promoted_fields": record.promoted_fields or {},
        "raw_metadata": record.raw_metadata or {},
        "extracted_at": extracted_at,
    }
    promoted = record.promoted_fields or {}
    for column_name in PROMOTED_COLUMN_NAMES:
        row[column_name] = coerce_promoted_db_value(promoted.get(column_name))
    return row
