from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.sql import text

_COMBINED_COLUMN_DDL: tuple[tuple[str, str], ...] = (
    ("phone_call_ticket_id", "BIGINT"),
    ("parent_link_key", "VARCHAR(512)"),
    ("ticket_form_id", "BIGINT"),
    ("zendesk_phone_call_fields", "JSONB DEFAULT '{}'::jsonb NOT NULL"),
    ("call_reason", "TEXT"),
    ("call_reason_code", "TEXT"),
    ("call_reason_source", "VARCHAR(128)"),
    ("disposition_code", "TEXT"),
    ("disposition_label", "TEXT"),
    ("disposition_source", "VARCHAR(128)"),
)


def ensure_combined_interaction_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "combined_interactions" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("combined_interactions")}
    missing = [(name, ddl) for name, ddl in _COMBINED_COLUMN_DDL if name not in existing]
    if not missing:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing:
            connection.execute(
                text(
                    f'ALTER TABLE combined_interactions '
                    f'ADD COLUMN IF NOT EXISTS "{column_name}" {column_type}'
                )
            )
