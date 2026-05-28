from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from orchestration.config import Settings
from orchestration.db.schema import ZendeskTicketCommentRow, init_database, utc_now
from orchestration.db.session import get_session_factory
from orchestration.models import TicketCommentRecord


class PostgresZendeskCommentSink:
    """Persist Zendesk ticket comment records to PostgreSQL."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_url = settings.database_url
        init_database(self._database_url)
        self._session_factory = get_session_factory(self._database_url)

    def upsert_records(self, records: list[TicketCommentRecord]) -> dict[str, int]:
        if not records:
            return {"upserted": 0}

        extracted_at = utc_now()
        rows = [_record_to_row(record, extracted_at) for record in records]

        with self._session_factory() as session:
            for row in rows:
                stmt = insert(ZendeskTicketCommentRow).values(**row)
                excluded = stmt.excluded
                update_columns = {
                    column.name: getattr(excluded, column.name)
                    for column in ZendeskTicketCommentRow.__table__.columns
                    if column.name not in ("comment_id", "row_created_at")
                }
                update_columns["row_updated_at"] = utc_now()
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ZendeskTicketCommentRow.comment_id],
                    set_=update_columns,
                )
                session.execute(stmt)
            session.commit()

        return {"upserted": len(records)}


def _record_to_row(record: TicketCommentRecord, extracted_at) -> dict:
    return {
        "comment_id": record.comment_id,
        "ticket_id": record.ticket_id,
        "author_id": record.author_id,
        "created_at": record.created_at,
        "is_public": record.is_public,
        "via_channel": record.via_channel,
        "body": record.body,
        "html_body": record.html_body,
        "plain_body": record.plain_body,
        "raw_metadata": record.raw_metadata or {},
        "extracted_at": extracted_at,
    }

