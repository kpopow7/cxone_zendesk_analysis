#!/usr/bin/env python3
"""Run daily CXone + Zendesk extracts and incremental combined_interactions update."""

from __future__ import annotations

import os
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
from orchestration.db.session import normalize_database_url  # noqa: E402
from orchestration.steps.daily_pipeline import (  # noqa: E402
    railway_classification_sync_args,
    railway_classification_sync_tables,
    railway_sync_cli_args,
    railway_sync_filter,
    railway_sync_tables,
    run_daily_knowledge_index,
    run_daily_pipeline,
    run_daily_reason_taxonomy,
)


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
@click.option(
    "--skip-classification",
    is_flag=True,
    help="Skip transcript classification + reduction report (default: run; needs OPENAI_API_KEY).",
)
@click.option(
    "--skip-reason-taxonomy",
    is_flag=True,
    help="Skip refreshing the canonical reason taxonomy map (default: run; no LLM needed).",
)
@click.option(
    "--skip-knowledge-index",
    is_flag=True,
    help="Skip rebuilding the chatbot RAG knowledge index (default: run; needs OPENAI_API_KEY).",
)
@click.option("--dry-run", is_flag=True, help="Run without writing to PostgreSQL.")
@click.option(
    "--sync-railway",
    is_flag=True,
    help="After pipeline, incrementally sync updated rows to Railway (needs TARGET_DATABASE_URL).",
)
def main(
    target_date_str: str | None,
    tz_name: str,
    zendesk_lookback_days: int,
    skip_cxone: bool,
    skip_zendesk: bool,
    skip_combined: bool,
    skip_classification: bool,
    skip_reason_taxonomy: bool,
    skip_knowledge_index: bool,
    dry_run: bool,
    sync_railway: bool,
) -> None:
    """Daily pipeline: extract -> combine -> classify (reasons + fixes) -> RAG index -> sync."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    target_date: date | None = None
    if target_date_str:
        target_date = date.fromisoformat(target_date_str)

    # When syncing to Railway, build the RAG index directly on the chatbot DB (the target)
    # after sync instead of locally, so we embed each day's content once.
    sync_railway_active = sync_railway and not dry_run
    build_index_on_target = sync_railway_active and not skip_knowledge_index

    result = run_daily_pipeline(
        settings=settings,
        target_date=target_date,
        tz_name=tz_name,
        zendesk_lookback_days=zendesk_lookback_days,
        skip_cxone=skip_cxone,
        skip_zendesk=skip_zendesk,
        skip_combined=skip_combined,
        skip_classification=skip_classification,
        skip_reason_taxonomy=skip_reason_taxonomy,
        skip_knowledge_index=skip_knowledge_index or build_index_on_target,
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
    if result.classification:
        report = result.classification.report
        click.echo(
            "Classification: "
            f"{report.totals.get('transcripts_classified_this_run', 0)} newly classified, "
            f"{report.totals.get('transcripts_analyzed', 0)} analyzed"
        )
        if result.classification.report_id is not None:
            click.echo(
                f"  Reduction report #{result.classification.report_id} -> "
                "analytics_reduction_recommendations"
            )
    if result.reason_taxonomy:
        tax = result.reason_taxonomy
        click.echo(
            f"Reason taxonomy: {tax.distinct_reasons} distinct reasons, "
            f"{tax.mapped_reasons} mapped, {tax.fallback_reasons} uncategorized"
        )
    if result.knowledge_index:
        idx = result.knowledge_index
        click.echo(
            f"Knowledge index: {idx.embedded} embedded, {idx.skipped_unchanged} unchanged, "
            f"{idx.errors} errors ({idx.candidates} candidates)"
        )

    if result.skipped_steps:
        click.echo(f"Skipped: {', '.join(result.skipped_steps)}")

    if sync_railway_active:
        _sync_to_railway(result, settings=settings, build_index_on_target=build_index_on_target)


def _run_sync(args: list[str]) -> None:
    sync_script = ROOT / "scripts" / "sync_to_railway.py"
    proc = subprocess.run(
        [sys.executable, str(sync_script), *args],
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise click.ClickException("Railway sync failed (see output above).")


def _sync_to_railway(result, *, settings, build_index_on_target: bool) -> None:
    target_day = result.window.cxone_start.date().isoformat()

    sync_tables = railway_sync_tables(result.skipped_steps)
    sync_filter = railway_sync_filter(result.window, result.skipped_steps)
    if sync_tables and sync_filter is not None:
        click.echo(f"Syncing to Railway ({', '.join(sync_tables)}, target day {target_day})...")
        _run_sync(railway_sync_cli_args(sync_filter, sync_tables))
        click.echo("Railway sync completed.")
    else:
        click.echo("Skipping relational Railway sync (no extract/combine steps ran).")

    class_tables = railway_classification_sync_tables(result.skipped_steps)
    if class_tables:
        click.echo(f"Syncing classification tables to Railway ({', '.join(class_tables)})...")
        _run_sync(railway_classification_sync_args(result.window, class_tables))
        click.echo("Classification sync completed.")

    # Rebuild the canonical reason map directly on Railway from the just-synced rows + config,
    # so the chatbot's canonical columns/views are correct (no extra table sync needed).
    if "reason_taxonomy" not in result.skipped_steps:
        target_url = os.environ.get("TARGET_DATABASE_URL")
        if target_url:
            click.echo("Refreshing reason taxonomy on Railway...")
            tax = run_daily_reason_taxonomy(
                settings, database_url=normalize_database_url(target_url)
            )
            click.echo(
                f"Railway reason taxonomy: {tax.distinct_reasons} distinct, "
                f"{tax.mapped_reasons} mapped, {tax.fallback_reasons} uncategorized"
            )

    if build_index_on_target:
        target_url = os.environ.get("TARGET_DATABASE_URL")
        if not target_url:
            raise click.ClickException(
                "TARGET_DATABASE_URL is required to build the knowledge index on Railway."
            )
        if not settings.openai_api_key:
            click.echo("Skipping knowledge index on Railway: OPENAI_API_KEY is not set.")
            return
        click.echo("Building knowledge index on Railway (chatbot DB)...")
        idx = run_daily_knowledge_index(
            settings, result.window, database_url=normalize_database_url(target_url)
        )
        click.echo(
            f"Railway knowledge index: {idx.embedded} embedded, "
            f"{idx.skipped_unchanged} unchanged, {idx.errors} errors"
        )


if __name__ == "__main__":
    main()
