#!/usr/bin/env python3
"""Step 1 CLI: daily CXone extract (list API only) into PostgreSQL.

For a one-time enriched historical load, use scripts/run_cxone_historical_backfill.py.
"""

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
from orchestration.steps.cxone_transcripts import run_cxone_transcript_extraction  # noqa: E402


@click.command()
@click.option(
    "--start",
    required=True,
    help="Range start (ISO-8601), e.g. 2026-05-01T00:00:00Z",
)
@click.option(
    "--end",
    required=True,
    help="Range end (ISO-8601), e.g. 2026-05-07T23:59:59Z",
)
@click.option("--dry-run", is_flag=True, help="Extract only; do not write to PostgreSQL.")
@click.option(
    "--skip-database",
    is_flag=True,
    help="Skip database write; optional JSON export only.",
)
@click.option("--limit", type=int, default=None, help="Max segments to process (testing).")
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
    """Extract CXone segments (list API) into PostgreSQL for incremental daily loads."""
    load_dotenv(ROOT / ".env")

    start_dt = parse_iso_datetime(start)
    end_dt = parse_iso_datetime(end)
    if end_dt <= start_dt:
        raise click.ClickException("--end must be after --start")

    result = run_cxone_transcript_extraction(
        start_dt,
        end_dt,
        dry_run=dry_run,
        skip_database=skip_database,
        enrich_transcripts=False,
        limit=limit,
        json_output=json_output,
    )

    click.echo(f"Segments extracted: {result.records_extracted}")
    if not dry_run and not skip_database:
        click.echo(f"Records upserted: {result.records_upserted}")
    if result.json_output_path:
        click.echo(f"JSON written: {result.json_output_path}")


if __name__ == "__main__":
    main()
