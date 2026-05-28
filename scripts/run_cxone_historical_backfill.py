#!/usr/bin/env python3
"""One-time CXone historical load with full analyzed-transcript enrichment per segment."""

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
from orchestration.steps.cxone_historical_backfill import run_cxone_historical_backfill  # noqa: E402


@click.command()
@click.option(
    "--start",
    required=True,
    help="Range start (ISO-8601), e.g. 2024-01-01T00:00:00Z",
)
@click.option(
    "--end",
    required=True,
    help="Range end (ISO-8601), e.g. 2026-05-01T23:59:59Z",
)
@click.option(
    "--chunk-days",
    type=int,
    default=1,
    show_default=True,
    help="Process the range in N-day windows (upsert after each chunk; safe to re-run).",
)
@click.option("--dry-run", is_flag=True, help="Extract only; do not write to PostgreSQL.")
@click.option(
    "--skip-database",
    is_flag=True,
    help="Skip database write (for testing API connectivity).",
)
@click.option(
    "--limit",
    type=int,
    default=None,
    help="Stop after this many segments total (testing).",
)
def main(
    start: str,
    end: str,
    chunk_days: int,
    dry_run: bool,
    skip_database: bool,
    limit: int | None,
) -> None:
    """Enriched historical backfill into cxone_transcripts (use run_cxone_extract.py for daily loads)."""
    load_dotenv(ROOT / ".env")

    if chunk_days < 1:
        raise click.ClickException("--chunk-days must be at least 1")

    start_dt = parse_iso_datetime(start)
    end_dt = parse_iso_datetime(end)
    if end_dt <= start_dt:
        raise click.ClickException("--end must be after --start")

    click.echo(
        f"Historical backfill: {start_dt.isoformat()} → {end_dt.isoformat()} "
        f"({chunk_days}-day chunks, enriched transcripts)"
    )

    result = run_cxone_historical_backfill(
        start_dt,
        end_dt,
        chunk_days=chunk_days,
        dry_run=dry_run,
        skip_database=skip_database,
        limit=limit,
    )

    for chunk in result.chunk_results:
        click.echo(
            f"  Chunk {chunk.chunk_index}: "
            f"{chunk.chunk_start.date()} → {chunk.chunk_end.date()} — "
            f"extracted {chunk.records_extracted}, upserted {chunk.records_upserted}"
        )

    click.echo(
        f"Done: {result.chunks_completed} chunk(s), "
        f"{result.records_extracted} segment(s) extracted"
    )
    if not dry_run and not skip_database:
        click.echo(f"Total upserted: {result.records_upserted}")


if __name__ == "__main__":
    main()
