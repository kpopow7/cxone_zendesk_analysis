#!/usr/bin/env python3
"""Copy analytic tables from local Postgres to Railway (or any target DATABASE_URL)."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.db.schema import (  # noqa: E402
    CombinedInteractionRow,
    CxoneTranscriptRow,
    ZendeskTicketRow,
    init_database,
)
from orchestration.db.session import get_engine, get_session_factory, normalize_database_url  # noqa: E402

TABLE_MODELS = {
    "cxone_transcripts": CxoneTranscriptRow,
    "zendesk_tickets": ZendeskTicketRow,
    "combined_interactions": CombinedInteractionRow,
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
    default="combined_interactions,zendesk_tickets,cxone_transcripts",
    show_default=True,
    help="Comma-separated tables to copy.",
)
@click.option("--batch-size", default=500, show_default=True)
@click.option("--init-schema/--no-init-schema", default=True, help="CREATE TABLE on target if missing.")
def main(
    source_url: str | None,
    target_url: str,
    tables: str,
    batch_size: int,
    init_schema: bool,
) -> None:
    """Upsert pipeline tables to Railway for the hosted chatbot."""
    load_dotenv(ROOT / ".env")
    if not source_url:
        from orchestration.config import get_settings

        source_url = get_settings().database_url

    source_url = normalize_database_url(source_url)
    target_url = normalize_database_url(target_url)

    table_names = [name.strip() for name in tables.split(",") if name.strip()]
    unknown = [name for name in table_names if name not in TABLE_MODELS]
    if unknown:
        raise click.ClickException(f"Unknown tables: {unknown}. Choose from {list(TABLE_MODELS)}")

    if init_schema:
        init_database(target_url)

    source_factory = get_session_factory(source_url)
    target_factory = get_session_factory(target_url)
    target_engine = get_engine(target_url)

    for table_name in table_names:
        model = TABLE_MODELS[table_name]
        click.echo(f"Syncing {table_name}...")
        copied = 0
        with source_factory() as src_session, target_factory() as tgt_session:
            stream = src_session.scalars(select(model)).yield_per(batch_size)
            batch: list = []
            for row in stream:
                batch.append(row)
                if len(batch) >= batch_size:
                    copied += _upsert_batch(tgt_session, model, batch, target_engine)
                    batch.clear()
            if batch:
                copied += _upsert_batch(tgt_session, model, batch, target_engine)
        click.echo(f"  {table_name}: {copied} rows upserted")

    click.echo("Done. Run scripts/railway_analytics_setup.sql on the target DB next.")


def _upsert_batch(session: Session, model, rows: list, engine) -> int:
    from sqlalchemy.dialects.postgresql import insert

    if not rows:
        return 0

    pk_names = [key.name for key in inspect(model).primary_key]
    values = []
    for row in rows:
        values.append({col.name: getattr(row, col.name) for col in model.__table__.columns})

    stmt = insert(model).values(values)
    update_cols = {
        col.name: stmt.excluded[col.name]
        for col in model.__table__.columns
        if col.name not in pk_names
    }
    stmt = stmt.on_conflict_do_update(index_elements=pk_names, set_=update_cols)
    session.execute(stmt)
    session.commit()
    return len(values)


if __name__ == "__main__":
    main()
