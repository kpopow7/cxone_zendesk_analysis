#!/usr/bin/env python3
"""Run daily CXone + Zendesk extracts and incremental combined_interactions update."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.config import get_settings  # noqa: E402
from orchestration.steps.daily_pipeline import run_daily_pipeline  # noqa: E402


@click.command()
@click.option(
    "--date",
    "target_date_str",
    default=None,
    help="Calendar day to process (YYYY-MM-DD). Default: yesterday in --timezone.",
)
@click.option(
    "--timezone",
    "tz_name",
    default="UTC",
    show_default=True,
    help="Timezone for the calendar day window (e.g. UTC, America/New_York).",
)
@click.option(
    "--zendesk-lookback-days",
    default=2,
    show_default=True,
    help="Also extract Zendesk tickets created N days before the target day (bridge tickets).",
)
@click.option("--skip-cxone", is_flag=True)
@click.option("--skip-zendesk", is_flag=True)
@click.option("--skip-combined", is_flag=True)
@click.option("--dry-run", is_flag=True, help="Run without writing to PostgreSQL.")
@click.option(
    "--sync-railway",
    is_flag=True,
    help="After pipeline, run scripts/sync_to_railway.py (needs TARGET_DATABASE_URL).",
)
def main(
    target_date_str: str | None,
    tz_name: str,
    zendesk_lookback_days: int,
    skip_cxone: bool,
    skip_zendesk: bool,
    skip_combined: bool,
    dry_run: bool,
    sync_railway: bool,
) -> None:
    """Daily pipeline: CXone transcripts -> Zendesk tickets -> combined_interactions."""
    load_dotenv(ROOT / ".env")

    target_date: date | None = None
    if target_date_str:
        target_date = date.fromisoformat(target_date_str)

    result = run_daily_pipeline(
        settings=get_settings(),
        target_date=target_date,
        tz_name=tz_name,
        zendesk_lookback_days=zendesk_lookback_days,
        skip_cxone=skip_cxone,
        skip_zendesk=skip_zendesk,
        skip_combined=skip_combined,
        dry_run=dry_run,
    )

    window = result.window
    click.echo(f"Daily pipeline window: {window.label}")
    click.echo(f"  CXone:     {window.cxone_start.isoformat()} -> {window.cxone_end.isoformat()}")
    click.echo(
        f"  Zendesk:   {window.zendesk_start.isoformat()} -> {window.zendesk_end.isoformat()}"
    )
    click.echo(
        f"  Combined:  {window.combined_start.isoformat()} -> {window.combined_end.isoformat()}"
    )

    if result.cxone:
        click.echo(f"CXone segments extracted: {result.cxone.records_extracted}")
        click.echo(f"CXone rows upserted: {result.cxone.records_upserted}")
    if result.zendesk:
        click.echo(f"Zendesk tickets extracted: {result.zendesk.records_extracted}")
        click.echo(f"Zendesk rows upserted: {result.zendesk.records_upserted}")
    if result.combined:
        click.echo(f"Combined segments considered: {result.combined.cxone_segments_considered}")
        click.echo(f"Combined rows upserted: {result.combined.rows_upserted}")
        click.echo(f"Combined matched: {result.combined.matched}")
        click.echo(f"Combined unmatched: {result.combined.unmatched}")

    if result.skipped_steps:
        click.echo(f"Skipped: {', '.join(result.skipped_steps)}")

    if sync_railway and not dry_run:
        click.echo("Syncing to Railway...")
        sync_script = ROOT / "scripts" / "sync_to_railway.py"
        proc = subprocess.run(
            [sys.executable, str(sync_script)],
            cwd=str(ROOT),
            check=False,
        )
        if proc.returncode != 0:
            raise click.ClickException("Railway sync failed (see output above).")
        click.echo("Railway sync completed.")


if __name__ == "__main__":
    main()
