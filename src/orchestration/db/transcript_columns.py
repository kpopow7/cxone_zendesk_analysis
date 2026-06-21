from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.sql import text

_TRANSCRIPT_COLUMN_DDL: tuple[tuple[str, str], ...] = (
    ("interaction_date", "DATE"),
)


def ensure_cxone_transcript_columns(engine: Engine) -> None:
    """Add derived columns to cxone_transcripts in place (no full re-extraction).

    Adds any missing columns, backfills interaction_date from interaction_start
    for existing rows where it is still NULL, and indexes interaction_date.
    Safe to run repeatedly: the backfill only touches rows that are not yet set.
    """
    inspector = inspect(engine)
    if "cxone_transcripts" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("cxone_transcripts")}
    missing = [(name, ddl) for name, ddl in _TRANSCRIPT_COLUMN_DDL if name not in existing]

    with engine.begin() as connection:
        for column_name, column_type in missing:
            connection.execute(
                text(
                    f'ALTER TABLE cxone_transcripts '
                    f'ADD COLUMN IF NOT EXISTS "{column_name}" {column_type}'
                )
            )

        connection.execute(
            text(
                """
                UPDATE cxone_transcripts
                SET interaction_date = (interaction_start AT TIME ZONE 'UTC')::date
                WHERE interaction_date IS NULL
                  AND interaction_start IS NOT NULL
                """
            )
        )

        connection.execute(
            text(
                'CREATE INDEX IF NOT EXISTS ix_cxone_transcripts_interaction_date '
                'ON cxone_transcripts (interaction_date)'
            )
        )
