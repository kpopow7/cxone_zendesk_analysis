#!/usr/bin/env python3
"""Step 2 CLI: extract Zendesk tickets for a created_at date range and load PostgreSQL."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.config import parse_iso_datetime  # noqa: E402
from orchestration.steps.zendesk_tickets import run_zendesk_ticket_extraction  # noqa: E402


@click.command()
@click.option(
    "--start",
    required=True,
    help="Range start (ISO-8601), filters ticket created_at, e.g. 2026-05-01T00:00:00Z",
)
@click.option(
    "--end",
    required=True,
    help="Range end (ISO-8601), e.g. 2026-05-08T00:00:00Z",
)
@click.option("--dry-run", is_flag=True, help="Extract only; do not write to PostgreSQL.")
@click.option(
    "--skip-database",
    is_flag=True,
    help="Skip database write; optional JSON export only.",
)
@click.option("--limit", type=int, default=None, help="Max tickets to process (testing).")
@click.option(
    "--json-output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write extracted records to a JSON file.",
)
def main(
    start: str,
    end: str,
    dry_run: bool,
    skip_database: bool,
    limit: int | None,
    json_output: Path | None,
) -> None:
    """Extract Zendesk tickets (created_at range) into PostgreSQL."""
    load_dotenv(ROOT / ".env")

    start_dt = parse_iso_datetime(start)
    end_dt = parse_iso_datetime(end)
    if end_dt <= start_dt:
        raise click.ClickException("--end must be after --start")

    result = run_zendesk_ticket_extraction(
        start_dt,
        end_dt,
        dry_run=dry_run,
        skip_database=skip_database,
        limit=limit,
        json_output=json_output,
    )

    click.echo(f"Tickets extracted: {result.records_extracted}")
    if not dry_run and not skip_database:
        click.echo(f"Records upserted: {result.records_upserted}")
    if result.json_output_path:
        click.echo(f"JSON written: {result.json_output_path}")


if __name__ == "__main__":
    main()
