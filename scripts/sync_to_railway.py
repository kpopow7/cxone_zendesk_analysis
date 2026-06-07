#!/usr/bin/env python3
"""Copy analytic tables from local Postgres to Railway (or any target DATABASE_URL)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

load_dotenv(ROOT / ".env")

from orchestration.db.analytics_views import ensure_analytics_views  # noqa: E402
from orchestration.db.schema import (  # noqa: E402
    CombinedInteractionRow,
    CxoneTranscriptAnalysisRow,
    CxoneTranscriptRow,
    ZendeskTicketRow,
    init_database,
)
from orchestration.db.session import get_engine, get_session_factory, normalize_database_url  # noqa: E402

TABLE_MODELS = {
    "cxone_transcripts": CxoneTranscriptRow,
    "cxone_transcript_analysis": CxoneTranscriptAnalysisRow,
    "zendesk_tickets": ZendeskTicketRow,
    "combined_interactions": CombinedInteractionRow,
}

# Large text/json columns blow up multi-row upserts on Railway Postgres.
# sql_overrides truncate at SELECT time so Postgres never materializes huge values.
TABLE_SYNC_DEFAULTS: dict[str, dict[str, Any]] = {
    "cxone_transcripts": {
        "batch_size": 25,
        "omit_columns": frozenset({"raw_metadata"}),
        "sql_overrides": {},
    },
    "combined_interactions": {
        "batch_size": 10,
        "omit_columns": frozenset({"zendesk_phone_call_fields"}),
        "sql_overrides": {
            "transcript_text": "left(transcript_text, 2000)",
            "ticket_description": "left(ticket_description, 800)",
        },
    },
    "cxone_transcript_analysis": {
        "batch_size": 200,
        "omit_columns": frozenset(),
        "sql_overrides": {},
    },
    "zendesk_tickets": {
        "batch_size": 100,
        "omit_columns": frozenset({"raw_metadata", "custom_fields"}),
        "sql_overrides": {
            "description": "left(description, 800)",
        },
    },
}


@click.command()
@click.option(
    "--source-url",
    envvar="SOURCE_DATABASE_URL",
    default=None,
    help="Source DB (default: DATABASE_URL from .env = local Docker).",
)
@click.option(
    "--target-url",
    envvar="TARGET_DATABASE_URL",
    required=True,
    help="Railway Postgres URL (Railway -> Postgres -> Connect).",
)
@click.option(
    "--tables",
    default="combined_interactions,zendesk_tickets,cxone_transcripts,cxone_transcript_analysis",
    show_default=True,
    help="Comma-separated tables to copy.",
)
@click.option(
    "--batch-size",
    default=None,
    type=int,
    help="Rows per upsert batch (overrides per-table defaults; use 10–25 for cxone_transcripts).",
)
@click.option(
    "--include-raw-metadata/--omit-raw-metadata",
    default=False,
    help="Include raw_metadata JSON on cxone_transcripts (large; omit by default).",
)
@click.option("--init-schema/--no-init-schema", default=True, help="CREATE TABLE on target if missing.")
def main(
    source_url: str | None,
    target_url: str,
    tables: str,
    batch_size: int | None,
    include_raw_metadata: bool,
    init_schema: bool,
) -> None:
    """Upsert pipeline tables to Railway for the hosted chatbot."""
    if not source_url:
        from orchestration.config import get_settings

        source_url = get_settings().database_url

    source_url = normalize_database_url(source_url)
    target_url = normalize_database_url(target_url)

    if "railway.internal" in target_url:
        raise click.ClickException(
            "TARGET_DATABASE_URL uses a Railway private hostname (postgres.railway.internal). "
            "That URL only works for services running on Railway, not from your PC.\n\n"
            "Fix: Railway dashboard -> Postgres service -> Connect -> copy the **public** "
            "URL (host like *.proxy.rlwy.net or *.railway.app, not *.railway.internal).\n"
            "Keep the private URL for DATABASE_URL on the deployed chatbot service only."
        )

    table_names = [name.strip() for name in tables.split(",") if name.strip()]
    unknown = [name for name in table_names if name not in TABLE_MODELS]
    if unknown:
        raise click.ClickException(f"Unknown tables: {unknown}. Choose from {list(TABLE_MODELS)}")

    if init_schema:
        click.echo("Ensuring target schema (tables + column migrations)...")
        init_database(target_url)

    target_engine = get_engine(target_url)
    click.echo("Ensuring analytics views on target...")
    ensure_analytics_views(target_engine)

    source_factory = get_session_factory(source_url)
    target_factory = get_session_factory(target_url)

    for table_name in table_names:
        model = TABLE_MODELS[table_name]
        table_defaults = TABLE_SYNC_DEFAULTS.get(
            table_name,
            {"batch_size": 500, "omit_columns": frozenset(), "sql_overrides": {}},
        )
        effective_batch = batch_size if batch_size is not None else int(table_defaults["batch_size"])
        omit_columns: frozenset[str] = table_defaults["omit_columns"]
        sql_overrides: dict[str, str] = table_defaults.get("sql_overrides", {})
        if include_raw_metadata and table_name == "cxone_transcripts":
            omit_columns = frozenset()

        omit_note = []
        if omit_columns:
            omit_note.append(f"omit {','.join(sorted(omit_columns))}")
        if sql_overrides:
            omit_note.append("truncate large text at source")
        extras = f", {', '.join(omit_note)}" if omit_note else ""

        click.echo(f"Syncing {table_name} (batch_size={effective_batch}{extras})...")
        copied = 0
        try:
            with source_factory() as src_session, target_factory() as tgt_session:
                copied = _sync_table_keyset(
                    src_session,
                    tgt_session,
                    model,
                    batch_size=effective_batch,
                    omit_columns=omit_columns,
                    sql_overrides=sql_overrides,
                )
        except Exception as exc:
            raise click.ClickException(_format_sync_error(table_name, exc, effective_batch)) from exc
        click.echo(f"  {table_name}: {copied} rows upserted")

    click.echo("Done.")


def _sync_table_keyset(
    src_session: Session,
    tgt_session: Session,
    model,
    *,
    batch_size: int,
    omit_columns: frozenset[str],
    sql_overrides: dict[str, str],
) -> int:
    """Paginate source rows by primary key to avoid loading the full table at once."""
    if sql_overrides or omit_columns:
        return _sync_table_keyset_sql(
            src_session,
            tgt_session,
            model,
            batch_size=batch_size,
            omit_columns=omit_columns,
            sql_overrides=sql_overrides,
        )

    pk_column = inspect(model).primary_key[0]
    pk_attr = getattr(model, pk_column.name)
    copied = 0
    last_pk: Any = None

    while True:
        stmt = select(model).order_by(pk_attr).limit(batch_size)
        if last_pk is not None:
            stmt = stmt.where(pk_attr > last_pk)

        batch = list(src_session.scalars(stmt).all())
        if not batch:
            break

        copied += _upsert_batch(tgt_session, model, batch, omit_columns=frozenset())
        last_pk = getattr(batch[-1], pk_column.name)
        src_session.expunge_all()

        if copied and copied % (batch_size * 20) == 0:
            click.echo(f"    ... {copied} rows so far", err=True)

    return copied


def _sync_table_keyset_sql(
    src_session: Session,
    tgt_session: Session,
    model,
    *,
    batch_size: int,
    omit_columns: frozenset[str],
    sql_overrides: dict[str, str],
) -> int:
    """Keyset sync with SQL-level truncation — avoids OOM on large text/json columns."""
    pk_column = inspect(model).primary_key[0]
    pk_name = pk_column.name
    table_name = model.__tablename__

    select_parts: list[str] = []
    for col in model.__table__.columns:
        if col.name in omit_columns:
            continue
        if col.name in sql_overrides:
            select_parts.append(f"{sql_overrides[col.name]} AS {col.name}")
        else:
            select_parts.append(col.name)

    copied = 0
    last_pk: Any = None

    while True:
        params: dict[str, Any] = {"batch_size": batch_size}
        where_clause = ""
        if last_pk is not None:
            where_clause = f"WHERE {pk_name} > :last_pk"
            params["last_pk"] = last_pk

        query = text(
            f"""
            SELECT {", ".join(select_parts)}
            FROM {table_name}
            {where_clause}
            ORDER BY {pk_name}
            LIMIT :batch_size
            """
        )
        rows = src_session.execute(query, params).mappings().all()
        if not rows:
            break

        copied += _upsert_batch(tgt_session, model, rows, omit_columns=omit_columns)
        last_pk = rows[-1][pk_name]

        if copied and copied % (batch_size * 20) == 0:
            click.echo(f"    ... {copied} rows so far", err=True)

    return copied


_OMIT_INSERT_DEFAULTS: dict[str, Any] = {
    "raw_metadata": {},
    "custom_fields": {},
    "zendesk_phone_call_fields": {},
}


def _row_values(row, model, omit_columns: frozenset[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for col in model.__table__.columns:
        if col.name in omit_columns:
            if col.name in _OMIT_INSERT_DEFAULTS:
                values[col.name] = _OMIT_INSERT_DEFAULTS[col.name]
            continue
        if isinstance(row, dict):
            values[col.name] = row.get(col.name)
        else:
            values[col.name] = getattr(row, col.name)
    return values


def _upsert_batch(
    session: Session,
    model,
    rows: list,
    *,
    omit_columns: frozenset[str],
) -> int:
    from sqlalchemy.dialects.postgresql import insert

    if not rows:
        return 0

    pk_names = [key.name for key in inspect(model).primary_key]
    values = [_row_values(row, model, omit_columns) for row in rows]

    stmt = insert(model).values(values)
    update_cols = {
        col.name: stmt.excluded[col.name]
        for col in model.__table__.columns
        if col.name not in pk_names and col.name not in omit_columns
    }
    stmt = stmt.on_conflict_do_update(index_elements=pk_names, set_=update_cols)
    session.execute(stmt)
    session.commit()
    return len(values)


def _format_sync_error(table_name: str, exc: Exception, batch_size: int) -> str:
    root = exc
    while root.__cause__ is not None:
        root = root.__cause__
    message = str(root) if root is not exc else str(exc)
    type_name = type(root).__name__
    if "UndefinedColumn" in type_name or "does not exist" in message:
        if "call_reason" in message or "disposition_label" in message:
            return (
                f"Sync failed on {table_name}: Railway Postgres is missing new normalized columns "
                f"(call_reason, disposition_label, etc.).\n\n"
                f"Re-run with schema init enabled (default):\n"
                f"  python scripts/sync_to_railway.py\n\n"
                f"If it still fails, run init manually then sync again:\n"
                f"  python -c \"from orchestration.db.schema import init_database; "
                f"init_database('YOUR_TARGET_URL')\""
            )
        return (
            f"Sync failed on {table_name}: target table schema is out of date.\n"
            f"Run: python scripts/sync_to_railway.py  (uses --init-schema by default)\n\n"
            f"Detail: {message[:500]}"
        )
    if "out of memory" in message.lower():
        hint = (
            f"  python scripts/sync_to_railway.py --tables {table_name} --batch-size 5"
        )
        if table_name == "combined_interactions":
            hint += (
                "\n\ncombined_interactions sync now truncates transcript_text to 2000 chars "
                "and omits zendesk_phone_call_fields. Full transcripts live in cxone_transcripts."
            )
        return (
            f"Sync failed on {table_name}: PostgreSQL ran out of memory "
            f"(batch_size={batch_size}).\n\n"
            f"Retry with a smaller batch:\n{hint}\n\n"
            f"Detail: {message[:500]}"
        )
    if len(message) > 800:
        message = message[:800] + "..."
    return f"Sync failed on {table_name}: {message}"


if __name__ == "__main__":
    main()
