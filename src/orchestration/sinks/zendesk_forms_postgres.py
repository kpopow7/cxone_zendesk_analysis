from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert

from orchestration.config import Settings
from orchestration.db.schema import ZendeskTicketFormRow, init_database, utc_now
from orchestration.db.session import get_session_factory


class PostgresZendeskFormSink:
    """Persist Zendesk ticket form definitions (form_id -> name) to PostgreSQL."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._database_url = settings.database_url
        init_database(self._database_url)
        self._session_factory = get_session_factory(self._database_url)

    def upsert_forms(self, records: list[dict[str, Any]]) -> dict[str, int]:
        if not records:
            return {"upserted": 0}

        extracted_at = utc_now()
        with self._session_factory() as session:
            for record in records:
                values = {**record, "extracted_at": extracted_at}
                stmt = insert(ZendeskTicketFormRow).values(**values)
                excluded = stmt.excluded
                update_columns = {
                    column.name: getattr(excluded, column.name)
                    for column in ZendeskTicketFormRow.__table__.columns
                    if column.name not in ("form_id", "row_created_at")
                }
                update_columns["row_updated_at"] = utc_now()
                stmt = stmt.on_conflict_do_update(
                    index_elements=[ZendeskTicketFormRow.form_id],
                    set_=update_columns,
                )
                session.execute(stmt)
            session.commit()

        return {"upserted": len(records)}
